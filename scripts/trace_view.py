"""追溯视图（A5）：把 memory_trace 的 JSON 生成一个静态 HTML 页面，浏览器直接打开，断网也能看。

版式和幻灯片一致：左边是按时间排列的记忆卡片，中间是回答（每个出处标记连线到对应卡片），
右边是新写入的记忆和它的来源链。调用者无权看的来源只显示“已隐藏 N 条”，不显示编号。

用法（仓库根目录）：
    # 用 mock 数据（memory_trace 上线前）
    python scripts/trace_view.py --answer tests/fixtures/trace/scenario2_answer.json \\
                                 --block tests/fixtures/trace/scenario2_b610.json
    # 接真实服务：按调用者的权限取数据（服务已运行；--agent 是 agents.json 里的 agent_id）
    python scripts/trace_view.py --agent zhou-local --answer c-0007 --block b-610

--answer、--block 可以是 JSON 文件，也可以是编号；至少给一个。输出默认写到 runs/trace-<编号>.html。
数据只经过服务（memory_trace），不直接读数据库。
"""

import argparse
import asyncio
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG = re.compile(r"\[([bw]-\d+)\]")

TEXT = {
    "zh": {"title": "记忆追溯", "answer": "回答", "citations": "引用（按重要性）", "memories": "记忆（按时间）",
           "new": "新写入的记忆", "chain": "来源链", "checked": "服务已核对：本会话读过",
           "hidden": "已隐藏 {n} 条来源（无权查看）", "viewer": "按 {agent} 的权限显示",
           "approved": "审批：{who} · {when}", "stale": "过时", "internal": "内部", "external": "外部",
           "no_sources": "原始记录，没有来源", "by": "提交者", "hidden_chip": "+{n} 已隐藏"},
    "en": {"title": "Memory trace", "answer": "Answer", "citations": "Citations (by importance)",
           "memories": "Memories (by time)", "new": "New memory", "chain": "Source chain",
           "checked": "Checked by the service: read in this session",
           "hidden": "{n} source(s) hidden (no access)", "viewer": "Shown with {agent}'s permissions",
           "approved": "Approved by {who} · {when}", "stale": "stale", "internal": "internal",
           "external": "external", "no_sources": "Original record, no sources", "by": "Submitted by",
           "hidden_chip": "+{n} hidden"},
}

CSS = """
:root{--bg:#F6F5F1;--card:#FDFDFB;--ink:#1F2A44;--muted:#5A6478;--blue:#2F6FB0;--orange:#B35A12;--line:#D9DCE3;--hl:#FFF4E5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang SC","Noto Sans SC",Inter,Arial,sans-serif}
header{padding:20px 28px 8px}h1{margin:0;font-size:24px}header p{margin:4px 0 0;color:var(--muted)}
main{position:relative;display:grid;grid-template-columns:minmax(260px,1fr) minmax(320px,1.3fr) minmax(260px,1fr);gap:48px;padding:16px 28px 40px}
main.two{grid-template-columns:minmax(260px,1fr) minmax(260px,1fr)}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
.col{display:flex;flex-direction:column;gap:14px;position:relative;z-index:1}
.card{background:var(--card);border:2px solid var(--line);border-radius:12px;padding:12px 14px;transition:border-color .15s,background .15s}
.card.root{border-color:var(--orange)}.card.on{border-color:var(--blue);background:var(--hl)}
.id{font:600 13px ui-monospace,Menlo,monospace;color:var(--blue)}.card h3{margin:2px 0 4px;font-size:16px}
.meta{color:var(--muted);font-size:13px}.content{margin:6px 0 0;font-size:14px}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.b{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
.b.internal{border-color:var(--orange);color:var(--orange)}.b.stale{background:#FBEBEB;color:#B3261E;border-color:#E8B4B0}
.answer{background:var(--card);border:2px solid var(--blue);border-radius:12px;padding:14px 16px;font-size:16px}
.mark{font:600 13px ui-monospace,Menlo,monospace;color:var(--blue);background:#E7EEF7;border-radius:6px;padding:0 5px;cursor:pointer;white-space:nowrap}
.mark.on{background:var(--blue);color:#fff}
ol.cites{margin:0;padding-left:22px}ol.cites li{margin:6px 0;padding:4px 8px;border-radius:8px;cursor:pointer}ol.cites li.on{background:var(--hl)}.ok{color:#2E7D4F;font-size:13px}
.hidden{border:2px dashed var(--line);border-radius:12px;padding:12px 14px;color:var(--muted);background:repeating-linear-gradient(45deg,#EEE 0 8px,#F6F5F1 8px 16px)}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{font:600 13px ui-monospace,Menlo,monospace;border:1px solid var(--line);border-radius:8px;padding:2px 8px;cursor:pointer}
svg.links{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0}
svg.links path{fill:none;stroke:#C3CBD8;stroke-width:2}svg.links path.on{stroke:var(--blue);stroke-width:3}
@media (max-width:900px){main,main.two{grid-template-columns:1fr}svg.links{display:none}}
"""

JS = """
const main=document.querySelector('main'),svg=document.querySelector('svg.links');
function center(el,side){const m=main.getBoundingClientRect(),r=el.getBoundingClientRect();
 return {x:(side==='right'?r.right:r.left)-m.left,y:r.top+r.height/2-m.top};}
function draw(){svg.innerHTML='';document.querySelectorAll('[data-link]').forEach(el=>{
 el.dataset.link.split(' ').forEach(id=>{const card=document.getElementById('card-'+id);if(!card)return;
  const a=center(card,'right'),b=center(el,'left'),dx=Math.max(40,(b.x-a.x)/2);
  const p=document.createElementNS('http://www.w3.org/2000/svg','path');
  p.setAttribute('d',`M${a.x},${a.y} C${a.x+dx},${a.y} ${b.x-dx},${b.y} ${b.x},${b.y}`);
  p.dataset.ids=id+' '+(el.dataset.self||'');svg.appendChild(p);});});}
function light(ids,on){ids.forEach(id=>{const c=document.getElementById('card-'+id);if(c)c.classList.toggle('on',on);
 document.querySelectorAll(`.mark[data-ref="${id}"],ol.cites li[data-ref="${id}"]`).forEach(m=>m.classList.toggle('on',on));
 svg.querySelectorAll('path').forEach(p=>{if(p.dataset.ids.split(' ').includes(id))p.classList.toggle('on',on);});});}
document.querySelectorAll('[data-ref]').forEach(el=>{const ids=el.dataset.ref.split(' ');
 el.addEventListener('mouseenter',()=>light(ids,true));el.addEventListener('mouseleave',()=>light(ids,false));});
addEventListener('load',draw);addEventListener('resize',draw);draw();
"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def load_trace(spec: str, agent: str | None) -> dict:
    """spec 是 JSON 文件路径，或者 answer_id / block_id（这时从服务的 memory_trace 取）。"""
    path = Path(spec)
    if path.suffix == ".json" or path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if not agent:
        sys.exit(f"{spec} 不是文件；从服务取数据时要用 --agent 指定调用者")
    return asyncio.run(_fetch(spec, agent))


async def _fetch(spec: str, agent_id: str) -> dict:
    sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
    from selftest import Agent
    from server.config import Config, load_agents

    cfg = Config.from_env()
    keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    arg = {"answer_id": spec} if spec.startswith("c-") else {"block_id": spec}
    async with Agent(f"http://{cfg.host}:{cfg.port}/mcp", keys[agent_id]) as mcp:
        result = await mcp.call("memory_trace", arg)
    if result.get("denied") or result.get("error"):
        sys.exit(f"memory_trace 被拒：{json.dumps(result, ensure_ascii=False)}")
    return result


def card(block_id: str, b: dict, t: dict, root: bool = False) -> str:
    exposure = b.get("exposure", "external")
    badges = [f'<span class="b">L{esc(b.get("level"))}</span>', f'<span class="b">{esc(b.get("scope"))}</span>',
              f'<span class="b {esc(exposure)}">{esc(t.get(exposure, exposure))}</span>']
    if b.get("stale"):
        badges.append(f'<span class="b stale">{t["stale"]}</span>')
    approved = (f'<div class="meta">{esc(t["approved"].format(who=b["approved_by"], when=b.get("approved_at", "")[:16].replace("T", " ")))}</div>'
                if b.get("approved_by") else "")
    content = f'<p class="content">{esc(b["content"])}</p>' if b.get("content") else ""
    return (f'<div class="card{" root" if root else ""}" id="card-{esc(block_id)}" data-ref="{esc(block_id)}">'
            f'<div class="id">{esc(block_id)}</div><h3>{esc(b.get("title", ""))}</h3>'
            f'<div class="meta">{esc(b.get("author", ""))} · {esc(str(b.get("created_at", ""))[:10])}</div>'
            f'{content}{approved}<div class="badges">{"".join(badges)}</div></div>')


def render(answer: dict | None, block: dict | None, lang: str = "zh", viewer: str | None = None) -> str:
    t = TEXT[lang]
    blocks = {**(answer or {}).get("blocks", {}), **(block or {}).get("blocks", {})}
    root_id = (block or {}).get("root")
    hidden = max((answer or {}).get("hidden_sources", 0), (block or {}).get("hidden_sources", 0))
    viewer = viewer or (answer or block or {}).get("agent", "")

    left = sorted((i for i in blocks if i != root_id), key=lambda i: str(blocks[i].get("created_at", "")))
    cols = [f'<section class="col"><h2>{t["memories"]}</h2>'
            + "".join(card(i, blocks[i], t) for i in left)
            + (f'<div class="hidden">{esc(t["hidden"].format(n=hidden))}</div>' if hidden else "") + "</section>"]

    if answer:
        def mark(m: re.Match) -> str:
            ref = m.group(1)
            return f'<span class="mark" data-ref="{ref}">[{ref}]</span>'
        body = TAG.sub(mark, esc(answer.get("answer") or ""))
        cites = "".join(
            f'<li data-ref="{esc(c["block_id"])}" data-link="{esc(c["block_id"])}"><span class="id">{esc(c["block_id"])}</span> '
            f'{esc(blocks.get(c["block_id"], {}).get("title", ""))}<br><span class="ok">✓ {t["checked"]}</span></li>'
            for c in sorted(answer.get("citations") or [], key=lambda c: c["rank"]))
        cols.append(f'<section class="col"><h2>{t["answer"]} · {esc(answer.get("answer_id"))}</h2>'
                    f'<div class="answer">{body}</div><h2>{t["citations"]}</h2><ol class="cites">{cites}</ol></section>')

    if root_id:
        rb = blocks[root_id]
        sources = rb.get("sources") or []
        # 有回答栏时，来源链的线会横穿回答，改成悬停高亮（chips 和卡片联动）；只有两栏时才画线
        link = f' data-link="{" ".join(esc(s) for s in sources)}" data-self="{esc(root_id)}"' if not answer else ""
        chips = "".join(f'<span class="chip" data-ref="{esc(s)}">{esc(s)}</span>' for s in sources)
        if hidden:
            chips += f'<span class="chip">{esc(t["hidden_chip"].format(n=hidden))}</span>'
        cols.append(f'<section class="col"><h2>{t["new"]}</h2>'
                    f'<div{link}>'
                    f'{card(root_id, rb, t, root=True)}</div>'
                    f'<h2>{t["chain"]}</h2><div class="chips">{chips or esc(t["no_sources"])}</div></section>')

    layout = "" if len(cols) == 3 else ' class="two"'
    title = f'{t["title"]} · {answer["answer_id"] if answer else root_id}'
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f'<style>{CSS}</style></head><body><header><h1>{esc(title)}</h1>'
            f'<p>{esc(t["viewer"].format(agent=viewer))}</p></header>'
            f'<main{layout}><svg class="links"></svg>{"".join(cols)}</main><script>{JS}</script></body></html>')


def main() -> None:
    parser = argparse.ArgumentParser(description="把 memory_trace 的结果生成追溯视图 HTML")
    parser.add_argument("--answer", help="回答的追溯：JSON 文件或 answer_id")
    parser.add_argument("--block", help="一条记忆的追溯：JSON 文件或 block_id")
    parser.add_argument("--agent", help="从服务取数据时用哪个 agent 的权限")
    parser.add_argument("--lang", choices=("zh", "en"), default="zh")
    parser.add_argument("--out", help="输出的 HTML 路径，默认 runs/trace-<编号>.html")
    args = parser.parse_args()
    if not (args.answer or args.block):
        parser.error("至少给 --answer 或 --block 其中一个")

    answer = load_trace(args.answer, args.agent) if args.answer else None
    block = load_trace(args.block, args.agent) if args.block else None
    page = render(answer, block, args.lang, args.agent)
    name = (answer or {}).get("answer_id") or (block or {}).get("root") or "trace"
    out = Path(args.out) if args.out else ROOT / "runs" / f"trace-{name}{'-' + args.agent if args.agent else ''}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
