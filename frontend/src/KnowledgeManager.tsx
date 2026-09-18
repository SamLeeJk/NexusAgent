import { useEffect, useState } from 'react';
import { api } from './api';

type Version = {id: string; title: string; number: number; content_hash: string; published_at: number | null};
type Document = {id: string; slug: string; active_version_id: string | null; versions: Version[]};
type Preview = Version & {document_id: string; content: string; chunks: {id: string; heading: string; text: string; start_line: number; end_line: number}[]};
type ImportResult = {document_id: string; version_id: string; deduplicated: boolean};

export function KnowledgeManager({onBack}: {onBack: () => void}) {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [slug, setSlug] = useState('');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [publishTarget, setPublishTarget] = useState<{doc: Document; version: Version} | null>(null);
  async function refresh() {setDocuments(await api<Document[]>('/admin/knowledge/documents'));}
  useEffect(() => {refresh().catch(e => setError(String(e.message)));}, []);
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {await action();} catch (e) {setError(e instanceof Error ? e.message : '操作失败');}
    finally {setBusy(false);}
  }
  async function showVersion(id: string) {
    setPublishTarget(null);
    setPreview(await api<Preview>(`/admin/knowledge/versions/${id}`));
  }
  async function importDocument() {
    const result = await api<ImportResult>('/admin/knowledge/documents', {slug, title, content});
    await refresh(); await showVersion(result.version_id);
    setNotice(result.deduplicated ? '此内容已存在，已打开原版本。' : '草稿已保存。核对下方内容后发布，客户才能检索。');
  }
  async function publish() {
    if (!publishTarget) return;
    const {doc, version} = publishTarget;
    await api(`/admin/knowledge/documents/${doc.id}/publish`, {version_id: version.id, expected_active_version_id: doc.active_version_id});
    await refresh(); await showVersion(version.id); setPublishTarget(null);
    setNotice(`已发布 ${version.title} v${version.number}。此前的会话引用仍可追溯。`);
  }
  return <main className="knowledge-page">
    <header className="knowledge-header"><div><p className="eyebrow">KNOWLEDGE LIBRARY</p><h1>知识库管理</h1><p className="muted">先预览，再发布。每次更新保留独立版本，支持回滚。</p></div><button disabled={busy} onClick={onBack}>返回工作台</button></header>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p className="knowledge-notice" role="status">{notice}</p>}
    <div className="knowledge-columns"><section>
      <h2>导入文档</h2><form className="knowledge-form" onSubmit={e=>{e.preventDefault(); void run(importDocument);}}>
        <label>文档标识<input value={slug} pattern="[a-z0-9][a-z0-9-]{0,79}" maxLength={80} placeholder="例如 refund-policy" onChange={e=>setSlug(e.target.value)} required /></label>
        <small>相同标识的文档生成新版本，不覆盖原文。</small>
        <label>文档标题<input value={title} maxLength={200} onChange={e=>setTitle(e.target.value)} required /></label>
        <label>导入 Markdown 文件<input type="file" accept=".md,.txt" disabled={busy} onChange={e=>{const file=e.target.files?.[0]; if(file) void run(async()=>{if(file.size>600000) throw new Error('文件过大，请控制在 200,000 字以内。'); const value=await file.text(); if(value.length>200000) throw new Error('文档超过 200,000 字。'); setContent(value);});}} /></label>
        <label htmlFor="knowledge-content">Markdown 正文</label><textarea id="knowledge-content" rows={10} value={content} maxLength={200000} onChange={e=>setContent(e.target.value)} required placeholder={'# 标题\n\n## 适用条件\n政策正文…'} />
        <button className="primary" disabled={busy || !content.trim()}>保存草稿并预览</button>
      </form>
      <h2 className="library-title">文档与版本</h2>
      {documents.map(doc=><section className="knowledge-document" key={doc.id}><h3>{doc.slug}</h3>{doc.versions.map(version=><div className="version-row" key={version.id}><div><strong>{version.title} · v{version.number}</strong><small>{doc.active_version_id===version.id?'当前发布':version.published_at?'历史版本':'草稿'}</small></div><div className="actions"><button disabled={busy} onClick={()=>void run(()=>showVersion(version.id))}>预览 v{version.number}</button><button disabled={busy || doc.active_version_id===version.id} onClick={()=>void run(async()=>{await showVersion(version.id); setPublishTarget({doc, version});})}>{version.published_at?'回滚至此版本':'发布此版本'}</button></div></div>)}</section>)}
    </section><section className="knowledge-preview"><h2>分块预览</h2>{preview ? <><h3>{preview.title} · v{preview.number}</h3><p className="muted">共 {preview.chunks.length} 个片段。正文按纯文本展示，不执行其中的指令或 HTML。</p>
      {publishTarget && <section className="publish-confirm" aria-label="发布确认"><strong>确认切换到 v{publishTarget.version.number}？</strong><p>客户将检索此版本；之前的引用保留为历史依据。此操作不会更改退款执行规则。</p><div className="actions"><button className="primary" disabled={busy} onClick={()=>void run(publish)}>确认发布</button><button disabled={busy} onClick={()=>setPublishTarget(null)}>取消发布</button></div></section>}
      {preview.chunks.map(chunk=><article className="knowledge-chunk" key={chunk.id}><h3>{chunk.heading || '正文'}</h3><small>第 {chunk.start_line}–{chunk.end_line} 行</small><pre>{chunk.text}</pre></article>)}
      <details><summary>查看完整原文</summary><pre>{preview.content}</pre></details>
    </> : <p className="muted">选择一个已有版本，或先导入文档。</p>}</section></div>
  </main>;
}
