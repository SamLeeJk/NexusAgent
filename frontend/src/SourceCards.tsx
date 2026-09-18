import { useState } from 'react';
import { api } from './api';

export type Source = {chunk_id: string; title: string; version: number; heading: string; text: string; start_line: number; end_line: number; is_current: boolean};

export function SourceCards({sources}: {sources: Source[]}) {
  const [selected, setSelected] = useState<Source | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function open(source: Source) {
    setBusy(true); setError('');
    try {setSelected(await api<Source>(`/knowledge/sources/${source.chunk_id}`));}
    catch(e) {setError(e instanceof Error ? e.message : '无法读取引用');}
    finally {setBusy(false);}
  }
  if (!sources.length) return null;
  return <div className="source-cards"><small>参考来源</small><div className="source-buttons">{sources.map(source=><button key={source.chunk_id} disabled={busy} onClick={()=>void open(source)}>{source.title} · v{source.version} · 第 {source.start_line}–{source.end_line} 行</button>)}</div>
    {error&&<p role="alert" className="error">{error}</p>}
    {selected&&<section className="source-detail" aria-label="引用原文"><div className="proposal-head"><strong>{selected.title} · v{selected.version}</strong><button onClick={()=>setSelected(null)}>收起原文</button></div><span className="badge">{selected.is_current?'当前发布版本':'历史版本，已非当前政策'}</span><h3>{selected.heading}</h3><pre>{selected.text}</pre><small>第 {selected.start_line}–{selected.end_line} 行；原文按引用时的版本保留。</small></section>}
  </div>;
}
