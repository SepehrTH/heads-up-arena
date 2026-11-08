#!/usr/bin/env python3
"""
viewer.py - browse the hands of an arena run in your web browser.

    python viewer.py                 # latest run in arena_runs/
    python viewer.py arena_runs/20261108-132008_someone__their-bot

Builds viewer.html inside the run folder and opens it. Every deal is shown as a duplicate
pair: half 1 (bot A holds one set of cards) next to half 2 (bot A holds the other set),
so you can see where the two bots played the same cards differently.
"""
import glob
import json
import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "arena_runs")


def latest_run():
    runs = sorted(d for d in glob.glob(os.path.join(RUNS, "*_*")) if os.path.isdir(d))
    if not runs:
        sys.exit("no runs found in arena_runs/ - play a match first")
    return runs[-1]


def load(run):
    matches = []
    for combined in sorted(glob.glob(os.path.join(run, "match_*.json"))):
        if combined.endswith(("_h1.json", "_h2.json")):
            continue
        seed = os.path.basename(combined)[len("match_"):-len(".json")]
        h1, h2 = (os.path.join(run, f"match_{seed}_h{h}.json") for h in (1, 2))
        if not (os.path.isfile(h1) and os.path.isfile(h2)):
            continue
        summary = json.load(open(combined))
        halves = [json.load(open(h1)), json.load(open(h2))]
        if not halves[0].get("logs"):
            continue
        deals = {}
        for hd in halves:
            me = hd["agent1_seat"]
            for h in hd["logs"]:
                deals.setdefault(h["deal_index"], [None, None])[hd["half"] - 1] = {
                    "a_seat": me, "button": h["button_seat"], "hole": h["hole_cards"],
                    "board": h["board"], "shown": len(h.get("board_revealed") or []),
                    "actions": h["actions"], "net": h["nets"][me], "pot": h["pot"],
                    "sd": h["went_to_showdown"]}
        matches.append({"seed": seed, "verdict": summary.get("verdict"), "mean": summary.get("mean_bb_per_deal"),
                        "ci": summary.get("ci"), "a1": summary.get("agent1_name", ""), "a2": summary.get("agent2_name", ""),
                        "deals": [{"i": k, "h": v} for k, v in sorted(deals.items())]})
    return matches


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Heads-Up Arena replay</title><style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mute:#6b6b66;--line:#e2e1dc;--card:#fff;--win:#1f7a3a;--lose:#b3261e;--accent:#2b5bd7}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#eceae4;--mute:#9a988f;--line:#2e2d2a;--card:#1d1d1b;--win:#5cc47a;--lose:#ff7a70;--accent:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
header{padding:20px 16px 8px;max-width:1100px;margin:auto}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mute)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;max-width:1100px;margin:8px auto;padding:0 16px}
select,button,label{font:inherit}select,button{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 9px;cursor:pointer}
button.on{border-color:var(--accent);color:var(--accent)}
main{max-width:1100px;margin:auto;padding:0 16px 40px}
.deal{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;overflow:hidden}
.top{display:grid;grid-template-columns:70px 1fr 1fr 90px;gap:8px;padding:8px 12px;cursor:pointer;align-items:center}
.top:hover{background:color-mix(in srgb,var(--accent) 6%,transparent)}
.num{color:var(--mute)}.tot{text-align:right;font-weight:600}
.pos{color:var(--win)}.neg{color:var(--lose)}
.c{display:inline-block;min-width:26px;padding:1px 4px;margin:1px;border:1px solid var(--line);border-radius:4px;text-align:center;font-weight:600;background:var(--bg)}
.s{color:#1d1d1b}.h{color:#c62828}.d{color:#1565c0}.cl{color:#2e7d32}
@media (prefers-color-scheme:dark){.s{color:#eceae4}.h{color:#ff6b6b}.d{color:#6ea8ff}.cl{color:#6fd47f}}
.dim{opacity:.35}.detail{display:none;border-top:1px solid var(--line);padding:10px 12px;display:none;gap:16px;grid-template-columns:1fr 1fr}
.open .detail{display:grid}.half h3{margin:0 0 6px;font-size:13px}.act{color:var(--mute);margin:2px 0}.act b{color:var(--fg);font-weight:600}
.lbl{color:var(--mute);font-size:12px}
@media (max-width:700px){.top{grid-template-columns:50px 1fr 70px}.top .h2c{display:none}.detail{grid-template-columns:1fr}}
</style></head><body>
<header><h1 id="title">Heads-Up Arena replay</h1><div class="sub" id="sub"></div></header>
<div class="bar"><select id="match"></select>
<button data-s="deal" class="on">by deal</button><button data-s="swing">biggest swings</button>
<label><input type="checkbox" id="sd"> showdowns only</label><span class="sub" id="count"></span></div>
<main id="list"></main>
<script>
const M = __DATA__;
const SUIT={s:'♠',h:'♥',d:'♦',c:'♣'}, CLS={s:'s',h:'h',d:'d',c:'cl'};
const card=(c,dim)=>`<span class="c ${CLS[c[1]]}${dim?' dim':''}">${c[0]}${SUIT[c[1]]}</span>`;
const cards=(a,shown)=>a.map((c,i)=>card(c,shown!==undefined&&i>=shown)).join('');
const bb=x=>(x/2>0?'+':'')+(x/2).toFixed(1)+' bb';
const cls=x=>x>0?'pos':(x<0?'neg':'');
let sort='deal', A='A', B='B';
const vtxt=m=>m.verdict==='AGENT1_WIN'?short(m.a1)+' wins':m.verdict==='AGENT2_WIN'?short(m.a2)+' wins':'chop';
const short=n=>(n||'').split(' (')[0].split(' / ')[0]||'?';
const esc=t=>String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function actions(h){
  const name=s=>s===h.a_seat?'<b>'+esc(A)+'</b>':esc(B); const words={FOLD:'folds',CHECK_CALL:'checks/calls',RAISE_HALF:'raises half pot',RAISE_POT:'raises pot'};
  let out=[],st='';
  for(const a of h.actions){ if(a.street!==st){st=a.street;out.push(`<div class="lbl">${st}</div>`)}
    let w=words[a.action]||a.action; if(a.action==='CHECK_CALL') w=a.amount>0?'calls '+a.amount:'checks';
    if(a.action.startsWith('RAISE')) w+=` (to ${a.street_total})`;
    out.push(`<div class="act">${name(a.seat)} ${w}</div>`)}
  return out.join('');
}
function half(h,n){
  if(!h) return '<div class="half">missing</div>';
  const ca=h.hole[h.a_seat], cb=h.hole[1-h.a_seat], btn=(h.button===h.a_seat?esc(A):esc(B))+' on button';
  return `<div class="half"><h3>Half ${n}: ${esc(A)} <span class="${cls(h.net)}">${bb(h.net)}</span> <span class="lbl">pot ${h.pot} · ${h.sd?'showdown':'no showdown'} · ${btn}</span></h3>
  <div><span class="lbl">${esc(A)} </span>${cards(ca)}</div><div><span class="lbl">${esc(B)} </span>${cards(cb)}</div>
  <div><span class="lbl">Board </span>${cards(h.board,h.sd?5:h.shown)}</div>${actions(h)}</div>`;
}
function render(){
  const m=M[+document.getElementById('match').value]; if(!m) return; A=short(m.a1)||'A'; B=short(m.a2)||'B'; if(A===B){A+=' (A)';B+=' (B)'}
  document.getElementById('sub').textContent=`${m.a1.split(' (')[0]}  vs  ${m.a2.split(' (')[0]}  ·  seed ${m.seed}  ·  ${vtxt(m)}  ·  ${A} ${m.mean>=0?'+':''}${m.mean.toFixed(3)} bb/deal  [${m.ci.map(x=>x.toFixed(2)).join(', ')}]`;
  let ds=m.deals.map(d=>({...d,tot:(d.h[0]?.net||0)+(d.h[1]?.net||0)}));
  if(document.getElementById('sd').checked) ds=ds.filter(d=>d.h.some(h=>h&&h.sd));
  if(sort==='swing') ds.sort((a,b)=>Math.abs(b.tot)-Math.abs(a.tot));
  document.getElementById('count').textContent=ds.length+' deals';
  document.getElementById('list').innerHTML=ds.slice(0,400).map(d=>{const a=d.h[0],b=d.h[1];
    return `<div class="deal"><div class="top"><div class="num">#${d.i}</div>
    <div>${a?cards(a.hole[a.a_seat]):''} <span class="lbl">H1</span> <span class="${cls(a?.net)}">${a?bb(a.net):''}</span></div>
    <div class="h2c">${b?cards(b.hole[b.a_seat]):''} <span class="lbl">H2</span> <span class="${cls(b?.net)}">${b?bb(b.net):''}</span></div>
    <div class="tot ${cls(d.tot)}">${bb(d.tot)}</div></div><div class="detail">${half(a,1)}${half(b,2)}</div></div>`}).join('')
    + (ds.length>400?`<p class="sub">showing first 400 of ${ds.length}</p>`:'');
  document.querySelectorAll('.top').forEach(t=>t.onclick=()=>t.parentElement.classList.toggle('open'));
}
const sel=document.getElementById('match');
M.forEach((m,i)=>sel.insertAdjacentHTML('beforeend',`<option value="${i}">match ${i+1} · seed ${m.seed} · ${vtxt(m)}</option>`));
sel.onchange=render; document.getElementById('sd').onchange=render;
document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{sort=b.dataset.s;document.querySelectorAll('[data-s]').forEach(x=>x.classList.toggle('on',x===b));render()});
render();
</script></body></html>"""


def main():
    run = sys.argv[1] if len(sys.argv) > 1 else latest_run()
    matches = load(run)
    if not matches:
        sys.exit(f"no hand logs in {run}. Hand histories are saved by arena.py runs from this version on.")
    out = os.path.join(run, "viewer.html")
    with open(out, "w") as fh:
        fh.write(PAGE.replace("__DATA__", json.dumps(matches, separators=(",", ":"))))
    print(f"wrote {out}  ({len(matches)} match(es))")
    webbrowser.open("file://" + os.path.abspath(out))


if __name__ == "__main__":
    main()
