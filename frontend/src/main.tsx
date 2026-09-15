import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { api } from './api';
import { KnowledgeManager } from './KnowledgeManager';
import { SourceCards, type Source } from './SourceCards';

type User = { id: string; username: string; role: string };
type Conversation = { id: string; created_at: number };
type Proposal = { id: string; order_id: string; status: string; expires_at: number; decision: 'approve' | 'reject' | null; result: string | null };
type Turn = { request_id: string; content: string; status: string };
type Snapshot = { id: string; messages: { id: string; role: string; content: string; sources?: Source[] }[]; proposals: Proposal[]; refunds: { id: string; order_id: string; status: string }[]; turns: Turn[] };
const statusName: Record<string, string> = { pending: '等待确认', approved: '正在恢复申请', completed: '已登记', rejected: '已取消', expired: '已过期', invalidated: '条件已变化' };

function App() {
  const [page, setPage] = useState<'chat' | 'knowledge'>('chat');
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [health, setHealth] = useState({mode: '', storage: ''});
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [orders, setOrders] = useState<{id: string; status: string}[]>([]);
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [retry, setRetry] = useState<{request_id: string; message: string; cid: string} | null>(null);
  const end = useRef<HTMLDivElement>(null);

  async function open(cid: string) {
    const value = await api<Snapshot>(`/conversations/${cid}`);
    setSnapshot(value); setRetry(null);
    sessionStorage.setItem('nexus.conversation', cid);
  }
  async function initialize(u: User) {
    setUser(u);
    const [list, own] = await Promise.all([api<Conversation[]>('/conversations'), api<{id: string; status: string}[]>('/orders')]);
    setConversations(list); setOrders(own);
    const saved = sessionStorage.getItem('nexus.conversation');
    const current = list.find(c => c.id === saved) ?? list[0];
    if (current) await open(current.id); else setSnapshot(null);
  }
  useEffect(() => {
    api<typeof health>('/health').then(setHealth).catch(() => setError('服务暂不可用，请检查后端。'));
    api<User>('/me').then(initialize).catch(() => setUser(null)).finally(() => setReady(true));
  }, []);
  useEffect(() => { end.current?.scrollIntoView({behavior: 'smooth'}); }, [snapshot, busy]);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，请重试。'); }
    finally { setBusy(false); }
  }
  async function newChat() {
    const value = await api<Conversation>('/conversations', {});
    setConversations(old => [value, ...old]); await open(value.id);
  }
  async function send(payload?: {request_id: string; message: string; cid: string}) {
    if (!snapshot || (!payload && !text.trim())) return;
    const request = payload ?? {request_id: crypto.randomUUID(), message: text.trim(), cid: snapshot.id};
    setRetry(request);
    const value = await api<Snapshot>(`/conversations/${request.cid}/messages`, {request_id: request.request_id, message: request.message});
    setSnapshot(value); setRetry(null); setText('');
  }
  async function decide(proposal: Proposal, decision: 'approve' | 'reject') {
    if (!snapshot) return;
    setSnapshot(await api<Snapshot>(`/conversations/${snapshot.id}/proposals/${proposal.id}/decision`, {decision}));
  }
  const pending = snapshot?.proposals.some(p => !p.result);
  const interrupted = snapshot?.turns.find(t => t.status === 'running');

  if (!ready) return <main className="loading">正在连接售后工作台…</main>;
  if (!user) return <main className="login-layout">
    <section className="intro"><div className="brand">N / NexusAgent</div><p className="eyebrow">CUSTOMER SUPPORT WORKSPACE</p><h1>让每一次售后，<br/>都有清晰的下一步。</h1><p>查询订单，找到政策依据，在你确认之后提交申请。<br/>每个会话和处理结果都会被保存。</p><div className="intro-note">查询 → 核对 → 确认 → 查看申请</div></section>
    <form className="login-form" onSubmit={e => {e.preventDefault(); void run(async () => {await initialize(await api<User>('/login', {username, password})); setPassword('');});}}>
      <p className="eyebrow">欢迎回来</p><h2>登录工作台</h2><p className="muted">仅展示当前账户有权访问的订单。</p>
      <label>用户名<input name="username" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required /></label>
      <label>密码<input name="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required /></label>
      {error && <p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy ? '正在登录…' : '进入工作台'}</button>
      {health.mode === 'demo' && <p className="demo-note">当前为规则演示模式，不调用真实模型。已初始化演示数据时可使用 alice / demo-alice-123。</p>}
    </form>
  </main>;
  if (page === 'knowledge' && user.role === 'admin') return <KnowledgeManager onBack={()=>setPage('chat')} />;
  return <div className="workspace">
    <aside className="sidebar"><div className="brand">N / NexusAgent</div><p className="eyebrow">售后工作台</p><button className="primary" disabled={busy} onClick={()=>void run(newChat)}>＋ 新建会话</button>
      {user.role==='admin' && <button className="knowledge-entry" disabled={busy} onClick={()=>setPage('knowledge')}>知识库管理</button>}
      <nav aria-label="会话列表">{conversations.map((c,i)=><button key={c.id} disabled={busy} className={snapshot?.id===c.id?'selected':''} onClick={()=>void run(()=>open(c.id))}><span>售后咨询 {conversations.length-i}</span><small>{new Date(c.created_at*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}</small></button>)}</nav>
      <div className="account"><span>{user.username}</span><button disabled={busy} onClick={()=>void run(async()=>{await api('/logout',{}); setUser(null); setSnapshot(null); setRetry(null); setText(''); sessionStorage.removeItem('nexus.conversation');})}>退出</button></div>
    </aside>
    <main className="chat"><header><div><p className="eyebrow">SUPPORT CONVERSATION</p><h1>订单与退款咨询</h1></div><span className="badge">{health.mode==='demo'?'规则演示':'AI 模型'} · 会话已保存</span></header>
      <div className="messages" aria-live="polite">
        {!snapshot?.messages.length && <section className="welcome"><span className="welcome-mark">N</span><h2>我们从哪张订单开始？</h2><p>你可以先询问退款资格。只有确认具体订单后，才会提交申请。</p><button disabled={busy} onClick={()=>void run(async()=>{if(!snapshot) await newChat(); setText('O1002 能退款吗？');})}>试试：O1002 能退款吗？</button></section>}
        {snapshot?.messages.map(m=><article key={m.id} className={`message ${m.role}`}><span className="speaker">{m.role==='user'?'你':'NexusAgent'}</span><p>{m.content}</p><SourceCards sources={m.sources ?? []}/></article>)}
        {snapshot?.proposals.map(p=><section className="proposal" key={p.id} aria-label={`订单 ${p.order_id} 确认卡`}><div className="proposal-head"><span className="eyebrow">退款申请</span><span className="badge">{statusName[p.status]??p.status}</span></div><h3>订单 {p.order_id}</h3><p>{p.result?'处理结果已保存，可以在此查看。':'将创建一条退款申请，不会直接执行资金退款。'}</p><small>确认有效期至 {new Date(p.expires_at*1000).toLocaleString('zh-CN')}</small>
          {!p.result && <div className="actions"><button className="primary" disabled={busy || p.decision==='reject'} onClick={()=>void run(()=>decide(p,'approve'))}>{p.decision==='approve'?'恢复已确认申请':'确认申请'}</button><button disabled={busy || p.decision==='approve'} onClick={()=>void run(()=>decide(p,'reject'))}>{p.decision==='reject'?'恢复取消结果':'取消'}</button></div>}
          {p.result && <p className="result">{p.result}</p>}
        </section>)}
        {busy && <p className="muted" role="status">正在处理，请稍候…</p>}<div ref={end}/>
      </div>
      <footer>{error&&<p role="alert" className="error">{error}</p>}
        {(retry || interrupted) && <button disabled={busy} onClick={()=>void run(()=>send(retry ?? {request_id: interrupted!.request_id, message: interrupted!.content, cid: snapshot!.id}))}>重试原消息（保持请求编号）</button>}
        <form onSubmit={e=>{e.preventDefault();void run(()=>send());}}><label className="sr-only" htmlFor="message">消息</label><textarea id="message" value={text} maxLength={10000} placeholder={pending?'请先处理上方的确认卡':'输入订单编号或售后问题…'} disabled={!snapshot || busy || pending || !!interrupted || !!retry} onChange={e=>setText(e.target.value)} rows={2}/><button className="primary" disabled={!snapshot || !text.trim() || busy || pending || !!interrupted || !!retry}>发送</button></form><small>会话自动保存。申请结果以服务端记录为准。</small>
      </footer>
    </main>
    <aside className="context"><p className="eyebrow">我的订单</p><h2>订单概览</h2>{orders.map(o=><button className="order" key={o.id} disabled={busy || pending} onClick={()=>setText(`${o.id} 能退款吗？`)}><strong>{o.id}</strong><span>{o.status==='processing'?'处理中':'已发货'}</span></button>)}<div className="context-note"><h3>确认前，先核对</h3><p>申请会绑定当前账户与订单。你可以取消本次操作，或在确认后查看申请编号。</p></div><small>演示系统不连接支付网关。</small></aside>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
