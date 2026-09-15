"""Owns graph lifecycle and crash-recoverable request/decision orchestration."""

from database import BusinessError
from langgraph.types import Command
from workflow import build_support_graph


class SupportService:
    def __init__(self, db, model, proposal_ttl=900):
        self.db, self.model, self.proposal_ttl = db, model, proposal_ttl

    def graph(self, saver):
        return build_support_graph(self.db, saver, self.model, self.proposal_ttl)

    @staticmethod
    def config(cid):
        return {"configurable": {"thread_id": cid}, "recursion_limit": 12}

    def send(self, uid, cid, request_id, content):
        self.db.own_conversation(uid, cid)
        with self.db.lock("conversation:" + cid):
            turn = self.db.begin_turn(uid, cid, request_id, content)
            if turn["status"] in ("completed", "waiting"):
                return self.db.snapshot(uid, cid)
            with self.db.checkpoint() as saver:
                graph, config = self.graph(saver), self.config(cid)
                previous = graph.get_state(config)
                if previous.values.get("turn_id") == turn["id"]:
                    # If checkpoint committed before HTTP bookkeeping, replay no work.
                    if previous.next:
                        graph.invoke(None, config)
                else:
                    graph.invoke(
                        {
                            "user_id": uid,
                            "conversation_id": cid,
                            "turn_id": turn["id"],
                            "text": content,
                            "proposal_id": None,
                            "reply": "",
                        },
                        config,
                    )
                state = graph.get_state(config)
                self.db.finish_turn(
                    cid, turn["id"], state.values["reply"], waiting=bool(state.next),
                    sources=state.values.get("sources", []),
                )
            return self.db.snapshot(uid, cid)

    def decide(self, uid, cid, pid, decision):
        self.db.own_conversation(uid, cid)
        with self.db.lock("conversation:" + cid):
            proposal = self.db.get_proposal(uid, cid, pid)
            with self.db.checkpoint() as saver:
                graph, config = self.graph(saver), self.config(cid)
                state = graph.get_state(config)
                if proposal["result"]:
                    if proposal["decision"] != decision:
                        raise BusinessError(409, "该提案已作出不同决定。")
                    # Finish a checkpoint left behind by a crash after the DB commit.
                    if state.values.get("proposal_id") == pid and state.next:
                        resume = (
                            Command(resume={"proposal_id": pid})
                            if any(t.interrupts for t in state.tasks)
                            else None
                        )
                        graph.invoke(resume, config)
                    self.db.finish_turn(
                        cid, proposal["turn_id"], proposal["result"], decision=True
                    )
                    return self.db.snapshot(uid, cid)
                if (
                    state.values.get("proposal_id") != pid
                    or state.values.get("turn_id") != proposal["turn_id"]
                ):
                    raise BusinessError(409, "提案与当前运行状态不一致，请重试原消息。")
                self.db.decide(uid, cid, pid, decision)
                if state.next:
                    if any(task.interrupts for task in state.tasks):
                        graph.invoke(Command(resume={"proposal_id": pid}), config)
                    else:
                        graph.invoke(None, config)
                result = graph.get_state(config).values["reply"]
                self.db.finish_turn(cid, proposal["turn_id"], result, decision=True)
            return self.db.snapshot(uid, cid)
