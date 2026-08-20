"""Shared offline graph viewer template."""

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>echr-py graph</title>
<style>
:root{font-family:Inter,system-ui,sans-serif;color:#172033;background:#f5f7fb}*{box-sizing:border-box}
body{margin:0;display:grid;grid-template-rows:auto 1fr;height:100vh}.top{background:#fff;border-bottom:1px solid #dfe4ee;padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}.title{font-weight:700}.stat{color:#64748b;font-size:12px}.warn{color:#9a3412;font-weight:600}.layout{display:grid;grid-template-columns:280px 1fr 300px;min-height:0}.panel{background:#fff;padding:14px;overflow:auto}.left{border-right:1px solid #dfe4ee}.right{border-left:1px solid #dfe4ee}label{display:block;font-size:11px;font-weight:700;color:#475569;margin:12px 0 4px}input,select{width:100%;padding:7px;border:1px solid #cbd5e1;border-radius:6px;background:#fff}.row{display:flex;gap:8px;align-items:center}.row input[type=checkbox]{width:auto}.row label{margin:0;font-weight:500}.canvas{position:relative;overflow:hidden}svg{width:100%;height:100%;background:#fbfcff}.link{stroke:#94a3b8;stroke-opacity:.55}.node{stroke:#fff;stroke-width:1.4;cursor:pointer}.node.dim,.link.dim{opacity:.08}.node.match{stroke:#f59e0b;stroke-width:4}.tooltip{position:absolute;pointer-events:none;background:#111827;color:#fff;padding:6px 8px;border-radius:5px;font-size:11px;display:none;max-width:320px}.legend{font-size:11px;line-height:1.7}.swatch{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}.details{font-size:12px;overflow-wrap:anywhere}.details dt{font-weight:700;margin-top:9px}.details dd{margin:2px 0;color:#475569}button{border:1px solid #cbd5e1;background:#fff;border-radius:6px;padding:7px 9px;cursor:pointer}@media(max-width:900px){.layout{grid-template-columns:220px 1fr}.right{display:none}}
</style><script type="text/plain" id="d3-license">__D3_LICENSE__</script><script>__D3_SOURCE__</script></head>
<body><div class="top"><span class="title" id="title">echr-py graph</span><span class="stat" id="stats"></span><span class="stat" id="warning"></span><button id="reset">Reset view</button></div>
<div class="layout"><aside class="panel left">
<label for="search">Search nodes</label><input id="search" placeholder="ID, label or attribute">
<label for="colorField">Colour by</label><select id="colorField"></select>
<label for="sizeField">Size by</label><select id="sizeField"></select>
<label for="filterField">Filter attribute</label><select id="filterField"></select>
<label for="filterValue">Filter value</label><select id="filterValue"></select>
<label for="component">Connected component</label><select id="component"></select>
<label for="weight">Minimum edge weight: <span id="weightValue">0</span></label><input id="weight" type="range" min="0" max="1" step="1" value="0">
<div class="row" style="margin-top:12px"><input id="isolates" type="checkbox"><label for="isolates">Hide isolated nodes</label></div>
<div class="row" style="margin-top:8px"><input id="arrows" type="checkbox" checked><label for="arrows">Show direction</label></div>
<label>Legend</label><div class="legend" id="legend"></div>
</aside><main class="canvas"><svg id="graph"><defs><marker id="arrow" viewBox="0 -5 10 10" refX="18" refY="0" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,-5L10,0L0,5" fill="#94a3b8"></path></marker></defs><g class="viewport"><g class="links"></g><g class="nodes"></g></g></svg><div class="tooltip" id="tooltip"></div></main>
<aside class="panel right"><h3 style="margin-top:0">Selection</h3><div class="details" id="details">Select a node or edge.</div></aside></div>
<script>
const bundle=__GRAPH_DATA__, originalNodes=bundle.nodes.map(d=>({...d})), originalLinks=bundle.links.map(d=>({...d}));
const meta=bundle.meta, svg=d3.select('#graph'), viewport=svg.select('.viewport'), linksG=viewport.select('.links'), nodesG=viewport.select('.nodes');
document.getElementById('title').textContent=`${meta.graph_id} · ${meta.kind}`;
document.getElementById('stats').textContent=`${meta.node_count.toLocaleString()} nodes · ${meta.edge_count.toLocaleString()} links${meta.directed?' · directed':''}`;
if(meta.pruned)document.getElementById('warning').textContent=`Showing top ${meta.node_count} of ${meta.original_node_count} nodes`;
else if(meta.node_count>10000)document.getElementById('warning').textContent='Large browser graph; consider --max-nodes';
const attrs=n=>({id:n.id,label:n.label||n.id,...(n.attributes||{})}), nodeRows=originalNodes.map(attrs);
const fields=[...new Set(nodeRows.flatMap(Object.keys))].sort(), numeric=fields.filter(k=>nodeRows.some(n=>Number.isFinite(+n[k])));
function options(id,values,first){const s=document.getElementById(id);s.innerHTML='';[first,...values].forEach(v=>{const o=document.createElement('option');o.value=v==='(none)'?'':v;o.textContent=v;s.appendChild(o)});}
options('colorField',fields,'(none)');options('sizeField',numeric,'(none)');options('filterField',fields,'(none)');options('filterValue',[],'(all)');
const adjacency=new Map(originalNodes.map(n=>[n.id,new Set()]));originalLinks.forEach(e=>{const s=String(e.source),t=String(e.target);if(adjacency.has(s)&&adjacency.has(t)){adjacency.get(s).add(t);adjacency.get(t).add(s)}});const componentByNode=new Map();let componentNumber=0;[...adjacency.keys()].sort().forEach(start=>{if(componentByNode.has(start))return;componentNumber++;const queue=[start];componentByNode.set(start,componentNumber);while(queue.length){const node=queue.shift();[...adjacency.get(node)].sort().forEach(next=>{if(!componentByNode.has(next)){componentByNode.set(next,componentNumber);queue.push(next)}})}});const componentSizes=new Map();componentByNode.forEach(value=>componentSizes.set(value,(componentSizes.get(value)||0)+1));const componentOptions=[...componentSizes].sort((a,b)=>b[1]-a[1]||a[0]-b[0]).map(([id,size])=>`${id} · ${size} nodes`);options('component',componentOptions,'(all)');
const weights=originalLinks.map(d=>+d.weight||1), maxWeight=Math.max(1,...weights);const wr=document.getElementById('weight');wr.max=maxWeight;wr.value=0;
const color=d3.scaleOrdinal(d3.schemeTableau10);let simulation;
function valueKey(v){return v==null?'(missing)':Array.isArray(v)?v.join('; '):typeof v==='object'?JSON.stringify(v):String(v)}
function updateFilterValues(){const f=document.getElementById('filterField').value;const vals=f?[...new Set(nodeRows.map(n=>valueKey(n[f])))].sort():[];options('filterValue',vals,'(all)');render();}
function render(){
 const search=document.getElementById('search').value.toLowerCase(), cf=document.getElementById('colorField').value, sf=document.getElementById('sizeField').value, ff=document.getElementById('filterField').value, fv=document.getElementById('filterValue').value, selectedComponent=(document.getElementById('component').value||'').split(' · ')[0], minW=+wr.value, hideIso=document.getElementById('isolates').checked;
 let nodes=originalNodes.filter(n=>(!ff||!fv||valueKey(attrs(n)[ff])===fv)&&(!selectedComponent||String(componentByNode.get(n.id))===selectedComponent)), allowed=new Set(nodes.map(n=>n.id));
 let links=originalLinks.filter(e=>allowed.has(String(e.source.id||e.source))&&allowed.has(String(e.target.id||e.target))&&(+e.weight||1)>=minW);
 if(hideIso){const connected=new Set(links.flatMap(e=>[String(e.source.id||e.source),String(e.target.id||e.target)]));nodes=nodes.filter(n=>connected.has(n.id));allowed=new Set(nodes.map(n=>n.id));links=links.filter(e=>allowed.has(String(e.source.id||e.source))&&allowed.has(String(e.target.id||e.target)));}
 const nums=sf?nodes.map(n=>+attrs(n)[sf]).filter(Number.isFinite):[], size=d3.scaleSqrt().domain(d3.extent(nums).length?d3.extent(nums):[0,1]).range([5,18]);
 const cats=cf?[...new Set(nodes.map(n=>valueKey(attrs(n)[cf])))]:[];color.domain(cats);
 linksG.selectAll('line').data(links,d=>d.id).join('line').attr('class','link').attr('stroke-width',d=>Math.max(1,Math.sqrt(+d.weight||1))).attr('marker-end',meta.directed&&document.getElementById('arrows').checked?'url(#arrow)':null).on('click',(e,d)=>showDetails({id:d.id,source:String(d.source.id||d.source),target:String(d.target.id||d.target),weight:d.weight,...d.attributes}));
 nodesG.selectAll('circle').data(nodes,d=>d.id).join('circle').attr('class',d=>'node'+(search&&JSON.stringify(attrs(d)).toLowerCase().includes(search)?' match':'')).attr('r',d=>sf?size(+attrs(d)[sf]):7).attr('fill',d=>cf?color(valueKey(attrs(d)[cf])):'#2563eb').on('click',(e,d)=>showDetails(attrs(d))).on('mousemove',(e,d)=>{const t=document.getElementById('tooltip');t.textContent=d.label||d.id;t.style.display='block';t.style.left=(e.offsetX+12)+'px';t.style.top=(e.offsetY+12)+'px'}).on('mouseout',()=>document.getElementById('tooltip').style.display='none').call(d3.drag().on('start',(e,d)=>{if(!e.active)simulation.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y}).on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y}).on('end',(e,d)=>{if(!e.active)simulation.alphaTarget(0);d.fx=null;d.fy=null}));
 if(simulation)simulation.stop();const rect=document.querySelector('.canvas').getBoundingClientRect();simulation=d3.forceSimulation(nodes).force('link',d3.forceLink(links).id(d=>d.id).distance(70).strength(.25)).force('charge',d3.forceManyBody().strength(-120)).force('center',d3.forceCenter(rect.width/2,rect.height/2)).force('collide',d3.forceCollide(13)).on('tick',()=>{linksG.selectAll('line').attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);nodesG.selectAll('circle').attr('cx',d=>d.x).attr('cy',d=>d.y)});
 document.getElementById('weightValue').textContent=minW;document.getElementById('legend').innerHTML=cf?cats.slice(0,30).map(c=>`<div><span class="swatch" style="background:${color(c)}"></span>${escapeHtml(c)}</div>`).join(''):'';
}
function escapeHtml(v){const d=document.createElement('div');d.textContent=v;return d.innerHTML}function showDetails(row){document.getElementById('details').innerHTML='<dl>'+Object.entries(row).map(([k,v])=>`<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(valueKey(v))}</dd>`).join('')+'</dl>'}
svg.call(d3.zoom().scaleExtent([.1,10]).on('zoom',e=>viewport.attr('transform',e.transform)));document.getElementById('reset').onclick=()=>svg.transition().duration(250).call(d3.zoom().transform,d3.zoomIdentity);
['search','colorField','sizeField','filterValue','component','weight','isolates','arrows'].forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',render));document.getElementById('filterField').addEventListener('change',updateFilterValues);render();
</script></body></html>"""

__all__ = ["HTML_TEMPLATE"]
