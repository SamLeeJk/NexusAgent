from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

ROOT=Path.cwd(); OUT=ROOT/'output/pdf/NexusAgent-A-Managers-Guide-to-AI-Agents.pdf'
for name,file in [('Body','arial.ttf'),('Bold','arialbd.ttf')]:
    pdfmetrics.registerFont(TTFont(name,'C:/Windows/Fonts/'+file))
W,H=595.28,841.89
C=canvas.Canvas(str(OUT),pagesize=(W,H))
C.setTitle('NexusAgent | A Manager\'s Guide to AI Agents')
C.setAuthor('NexusAgent project')
INK='#182F3A'; TEAL='#146D66'; MUTED='#536B77'; PALE='#EDF5F2'; LINE='#D7E3E6'; AMBER='#FBF3E4'
styles={}
for key,size,leading,color,font in [('body',11,16,INK,'Body'),('small',9,13,MUTED,'Body'),('h2',15,20,INK,'Bold'),('title',29,35,INK,'Bold'),('quote',17,23,TEAL,'Bold'),('white',12,17,'#FFFFFF','Bold')]:
    styles[key]=ParagraphStyle(key,fontName=font,fontSize=size,leading=leading,textColor=HexColor(color),spaceAfter=0)
pdfmetrics.registerFontFamily('Body',normal='Body',bold='Bold',italic='Body',boldItalic='Bold')
def p(text,x,y,w=499,style='body'):
    obj=Paragraph(text,styles[style]); _,height=obj.wrap(w,740)
    assert y+height<786,(page,text[:60],y,height)
    obj.drawOn(C,x,H-y-height)
    return y+height

def line(y):
    C.setStrokeColor(HexColor(LINE));C.line(48,H-y,547,H-y)
def begin(n,kicker,title,sub):
    global page
    page=n
    C.setFillColor(HexColor(TEAL));C.rect(0,H-9,W,9,fill=1,stroke=0)
    p('NEXUSAGENT   /   MANAGER BRIEF',48,30,style='small')
    p(kicker.upper(),48,76,style='small')
    end=p(title,48,100,style='title')
    p(sub,48,end+15,style='body')
    line(767)
    p('PROJECT SNAPSHOT  /  17 SEP 2026',48,783,style='small') if False else None
    C.setFont('Body',8);C.setFillColor(HexColor(MUTED));C.drawString(48,38,'NexusAgent  |  Project snapshot: 17 September 2026')
    C.drawRightString(547,38,f'{n} / 6')
def box(title,text,y,height=105,color=PALE):
    C.setFillColor(HexColor(color));C.roundRect(48,H-y-height,499,height,10,fill=1,stroke=0)
    p(title,64,y+14,467,'h2'); end=p(text,64,y+43,467)
    assert end<=y+height-10,(title,end,y+height)
def section(title,text,y):
    y=p(title,48,y,style='h2')+7
    return p(text,48,y)+22

def arrow(x1,y1,x2,y2):
    C.setStrokeColor(HexColor(TEAL));C.setLineWidth(1.7);C.line(x1,H-y1,x2,H-y2)
    import math
    a=math.atan2(y2-y1,x2-x1)
    for delta in (-.5,.5):
        C.line(x2,H-y2,x2-8*math.cos(a+delta),H-(y2-8*math.sin(a+delta)))
def node(x,y,w,title,text):
    C.setFillColor(HexColor(PALE));C.roundRect(x,H-y-88,w,88,8,fill=1,stroke=0)
    p(title,x+12,y+12,w-24,'h2');p(text,x+12,y+40,w-24,'small')

begin(1,'A six-minute introduction','From a question<br/>to a controlled action','One customer-support case explains AI agents, LangChain, LangGraph and the agent loop. No AI background required.')
box('The customer\'s goal','Alice asks: <b>"Can I get a refund for order O1002?"</b><br/>She wants a correct answer and, if eligible, a way to submit a request.',235,112)
y=section('What is an AI agent?', 'An AI agent is software that uses a language model to interpret a goal and help choose actions. It can use approved tools, examine their results and continue until it can answer, complete a task or ask a person for help.',378)
y=section('What makes this more than a text answer?', 'A text-only model can explain a refund policy. A connected agent system can check Alice\'s actual order, keep track of the conversation, request permission and register a refund application through controlled software.',y)
box('The boundary that matters','The model helps interpret the request. The application enforces permissions and business rules. <b>Submitting a refund request does not move money.</b>',588,110)
p('Scope note: NexusAgent currently runs a rule-based demo. Its model integration exists, but live-model performance has not been validated. The case below explains the design, not a claim of autonomous production operation.',48,716,499,'small')
C.showPage()

begin(2,'The use case','Follow Alice\'s request','Illustrative English dialogue. The current interface and many responses are in Chinese; the business sequence is the same.')
steps=[
('01  Ask about eligibility','Alice: "Can O1002 be refunded?"','The system checks the signed-in account and reads the order. O1002 is processing, so it can be considered for a refund request. Nothing is submitted.'),
('02  Ask for the policy','Alice: "What is the confirmation policy?"','The system retrieves published policy text and shows its document version and source lines. The order database still decides actual eligibility.'),
('03  Request an action','Alice: "Please apply for O1002."','The system creates a proposal tied to this user and order, then displays a confirmation card. It saves progress and waits; there is still no refund request.'),
('04  Give explicit permission','Alice clicks the confirmation button.','The server records her decision, resumes the workflow and rechecks ownership, order status and expiry before writing the request.'),
('05  Receive a result','Alice sees a saved application reference.','The request is registered, not paid out. Retrying the same confirmed operation returns the saved result rather than creating another application.')]
y=214
for title,quote,text in steps:
    p(title,48,y,499,'h2');p(quote,48,y+26,499,'small');p(text,48,y+44,499,'body');y+=104
p('Other valid outcomes: an inaccessible or ineligible order is declined; cancellation creates no request; an expired confirmation requires a new proposal.',48,740,499,'small')
C.showPage()

begin(3,'The execution pattern','What is an agent loop?','A loop is repeated decision-making informed by new evidence. It is not simply generating a longer answer.')
node(48,216,226,'1. Inspect','Read the goal, conversation and available evidence.')
node(321,216,226,'2. Choose','Select an allowed next action, ask a question or finish.')
node(321,352,226,'3. Act','Run an approved tool, such as looking up an order.')
node(48,352,226,'4. Observe','Read the tool result and update the working state.')
arrow(277,260,316,260);arrow(434,309,434,347);arrow(316,396,279,396);arrow(160,347,160,309)
p('Repeat only if more work is needed. Stop to answer, wait for approval, or report a failure.',48,463,499,'small')
y=section('In Alice\'s case', 'The order lookup returns "processing". That evidence supports an eligibility answer. A later request to apply leads to a confirmation step, not immediate execution. Human permission becomes the next required input.',503)
box('How NexusAgent uses this idea','Its active path is a <b>bounded, stateful workflow</b>: understand → resolve → approval → execute. Policy and inquiry paths can finish after resolve. It does not currently run an unrestricted model/tool loop.',615,118)
p('The loop above describes the general agent pattern. NexusAgent deliberately fixes sensitive transitions in code. A new message or approval can start another saved workflow invocation. [3]',48,739,499,'small')
C.showPage()

begin(4,'The building blocks','LangChain and LangGraph','Neither is the AI model. They are software building blocks around the model, with different responsibilities.')
box('The language model: interpret the request','In model mode, it classifies Alice\'s message as an order inquiry, policy question, refund application or request needing clarification. It is not allowed to approve its own action.',215,111)
box('LangChain: connect the model to the application','LangChain provides model integrations and agent-building interfaces. NexusAgent uses its model integration to obtain a structured intent, such as "refund" plus an order ID, rather than relying on unrestricted prose. [1]',345,130)
box('LangGraph: control and remember the process','LangGraph is the workflow runtime. Nodes are steps; edges define where execution goes next; state is the saved case information. A checkpoint stores progress, so approval can pause execution and a later request can resume it. [2]',494,130)
p('<b>For Alice:</b> model interpretation identifies her intent; LangChain connects that interpretation to code; LangGraph controls when the system checks, pauses and executes. PostgreSQL stores the business records and durable workflow checkpoints.',48,649)
p('Relationship: LangChain\'s standard agents are built on LangGraph. This project uses a custom LangGraph workflow with a LangChain model adapter, not the standard create_agent loop. [1]',48,725,499,'small')
C.showPage()

begin(5,'Controls and visibility','What makes an action safe?','The useful capability is controlled execution with evidence, permission and recovery. A fluent answer alone does not provide those guarantees.')
y=212
for title,text in [
('Tool access is limited','A tool is a software function that reads data or performs a permitted action. Here, order access is scoped to the signed-in customer; a model cannot grant itself access to another account.'),
('State is a saved case file','Conversation state remembers the current order and pending proposal. A checkpoint records workflow progress. This is stored application context, not a model learning or retraining itself.'),
('Approval is a real gate','The confirmation is bound to the user, order and proposal, and has a 15-minute expiry. A statement such as "the user agreed" generated by a model is not authorization.'),
('A retry does not mean a second refund','The server rechecks the order inside the execution path. Transactions and uniqueness constraints protect writes; request IDs and saved receipts support safe retries. This is the meaning of idempotency.'),
('Operations can be inspected','A trace follows one request through its steps; metrics show counts, errors and durations; structured logs carry correlation IDs. Normal waiting is distinguished from failure, without collecting passwords or full prompts.')]:
    y=section(title,text,y)
p('Knowledge retrieval means looking up approved text before answering. Current search is Chinese/English lexical matching, not vector-based semantic search; citations preserve the published version used.',48,719,499,'small')
C.showPage()

begin(6,'A practical discussion','What should a manager assess?','NexusAgent demonstrates the engineering controls around an agent-enabled service. Business impact still needs to be measured in a real pilot.')
y=section('A three-minute demonstration','1. Ask about O1002 and inspect the policy source.<br/>2. Request an application, then show the explicit confirmation gate.<br/>3. Refresh while waiting, confirm, and inspect the saved result.<br/>4. Retry the same action and show that no duplicate is created.<br/>5. Open its trace to see where time was spent or a failure occurred.',207)
y=section('What is delivered, and what is not yet proven?', 'Delivered: saved conversations, approval and recovery, transactional requests, versioned knowledge, citations and observability. The local demo uses deterministic rules with simulated orders. A live-model adapter exists; model quality, production scale and customer outcomes remain unvalidated. There is no payment-gateway integration.',y)
y=section('How to judge a future pilot', 'Measure correct resolution rate, unauthorized-action attempts blocked, duplicate actions prevented, time per case, escalation rate and cost per resolved case. Compare against a human or existing workflow baseline. No time-saving, revenue or ROI improvement is claimed here.',y)
p('The management takeaway',48,590,499,'h2')
p('The model interprets. Tools access business systems. LangChain connects components. LangGraph manages the process. Your application retains authority over the action.',48,617,499,'body')
line(678)
p('Sources and project evidence',48,689,499,'small')
sources=[('[1] LangChain overview','https://docs.langchain.com/oss/python/langchain/overview'),('[2] LangGraph overview','https://docs.langchain.com/oss/python/langgraph/overview'),('[3] Workflows and agents','https://docs.langchain.com/oss/python/langgraph/workflows-agents')]
x=48
for label,url in sources:
    p(f'<link href="{url}" color="{TEAL}">{label}</link>',x,708,166,'small');x+=169
p('Project evidence: README; docs/PROGRESS.md; backend/workflow.py, service.py and database.py. Framework definitions checked against official documentation. Dialogue is illustrative; scope reflects the repository snapshot.',48,731,499,'small')
C.showPage();C.save()
print(OUT)
