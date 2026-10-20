"""
Bifrost v1 — browser shim + NullCash re-branding layer for the Veil mirror.

Veil's own client code still runs unmodified in the browser (wallet creation,
ZK proofs, RPC, relayer, indexer). This module only:
  * injects a shim that routes every network call through /api/bifrost/v1/*
  * keeps the URL bar on pretty paths (/shield, /shield/about, /shield/docs …)
  * swaps visible brand text / logos / colours to NullCash
"""
import base64
import functools
from pathlib import Path

BRAND_DIR = Path(__file__).parent / "brand"
BRAND_NAME = "NullCash"


def _b64(name: str) -> str:
    try:
        return base64.b64encode((BRAND_DIR / name).read_bytes()).decode()
    except Exception:
        return ""


@functools.lru_cache(maxsize=4)
def lockup_svg(light: bool = False) -> str:
    mark = _b64("mark-black-256.png" if light else "mark-white-256.png")
    word = "#12140f" if light else "#f4f6ee"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 470 128" width="470" height="128">
<image href="data:image/png;base64,{mark}" xlink:href="data:image/png;base64,{mark}" x="0" y="8" width="112" height="112"/>
<text x="130" y="86" font-family="'Space Grotesk', Inter, 'Segoe UI', Helvetica, Arial, sans-serif" font-size="64" font-weight="700" letter-spacing="-2.5" fill="{word}">Null<tspan font-weight="500">Cash</tspan></text>
</svg>"""


BRAND_CSS = """
/* project token removed: only swap tokens remain */
/* third-party / upstream brand surfaces removed (extension promo, audit badges, upstream socials) */
a.xc,.xc,a[href*="chromewebstore.google.com"],
a[href*="t.me/veil"],a[href*="github.com/veil"],a[href*="@veilit.xyz"],.foot-audit,.batch-tag[href*="contractwolf.io"],
img[src*="audit-contractwolf"],li:has(> a[href*="contractwolf.io"]),#extension,.ext-soon,.ext-get,
section#developers,a[href="#developers"],a[href$="/v1/app/api"],a[href*="/v1/app/api#"],a[href*="/v1/app/api?"],a[href="/app/api"],a[href^="/app/api#"],a[href$="docs#developers"],a[href="#extension"]{display:none!important}
a[href*="contractwolf.io"]{pointer-events:none!important;color:inherit!important;text-decoration:none!important}
.omark,.tk,.foot-ca,[data-copy="HgTGdc29D5bDMrDvsCLnorxz899VyDj7ZN7FPa6vpump"]{display:none!important}
html:not(.bf-ready) body{opacity:0}
html.bf-ready body{opacity:1;transition:opacity .18s ease}
img[src*="_brand/lockup"]{object-fit:contain;height:26px!important;filter:none!important;width:auto!important;max-width:none!important}
"""

_SHIM = r"""
(function(){
var P='__P__',R='__R__',T='__T__',PRETTY=T==='site'?'/shield/about':'/shield',BRAND='__BRAND__';
var ORIGIN=location.origin,BU=R+'_brand/';
try{localStorage.setItem('cb-theme','light');}catch(e){}
var FL=function(){var h=document.documentElement;if(h.getAttribute('data-theme')!=='light')h.setAttribute('data-theme','light');};FL();try{new MutationObserver(FL).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});}catch(e){}
function isLight(){var th=document.documentElement.getAttribute('data-theme');if(th==='light')return true;if(th==='dark')return false;
 try{var els=[document.body,document.documentElement];for(var i=0;i<els.length;i++){if(!els[i])continue;var c=getComputedStyle(els[i]).backgroundColor;var m=c&&c.match(/rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:[, /]+([\d.]+))?/);
 if(m&&(m[4]===undefined||parseFloat(m[4])>0.05)){return (0.2126*m[1]+0.7152*m[2]+0.0722*m[3])/255>0.55;}}}catch(e){}return false;}
function LK(){return BU+(isLight()?'lockup-light.svg':'lockup.svg');}
function up(p){
  if(p.indexOf(R)===0)return p;
  if(p==='/about'||p.indexOf('/about/')===0)return R+'site'+(p.slice(6)||'/');
  if(p==='/app'||p.indexOf('/app/')===0)return R+'app'+(p.slice(4)||'/');
  if(p==='/shield/about'||p.indexOf('/shield/about/')===0)return R+'site'+(p.slice(13)||'/');
  if(p==='/shield'||p.indexOf('/shield/')===0)return R+'app'+(p.slice(7)||'/');
  return P+p;
}
function vbaseFrom(pathname){return ORIGIN+(pathname.indexOf(R)===0?pathname:up(pathname));}
var VBASE=vbaseFrom(location.pathname)+location.search;
self.TURBOPACK_CHUNK_BASE_PATH=P+'/_next/';
var SKIP=/^(data:|blob:|javascript:|mailto:|about:|tel:|#)/i;
function fx(u){
  if(typeof u!=='string'||!u||SKIP.test(u))return u;
  var x;try{x=new URL(u,VBASE);}catch(e){return u;}
  if(x.origin!==ORIGIN)return u;
  return up(x.pathname)+x.search+x.hash;
}
function pretty(u){
  var x;try{x=new URL(String(u),location.href);}catch(e){return u;}
  if(x.origin!==ORIGIN)return u;
  var p=x.pathname,q;
  if(p.indexOf(R+'app')===0)q='/app'+p.slice((R+'app').length);
  else if(p.indexOf(R+'site')===0)q='/about'+p.slice((R+'site').length);
  else if(p.indexOf('/shield/about')===0)q='/about'+p.slice(13);
  else if(p.indexOf('/shield')===0)q='/app'+p.slice(7);
  else if(p==='/app'||p.indexOf('/app/')===0||p==='/about'||p.indexOf('/about/')===0)q=p;
  else q=(T==='site'?'/about':'/app')+p;
  q=q.replace(/\/+$/,'')||'/';
  return q+x.search+x.hash;
}
try{if(T==='site'){location.replace('/');}
 else{var sub=(function(){var lp=location.pathname;if(lp.indexOf(P)===0)return lp.slice(P.length)||'/';if(lp.indexOf('/app')===0)return lp.slice(4)||'/';return lp;})();if(/^\/api(\/|$)/.test(sub)){location.replace('/docs');}else if(/^\/(docs|security|launch|compliance|privacy|terms)(\/|$)/.test(sub)){location.replace(/^\/security/.test(sub)?'/#zk':'/#tech');}}}catch(e){}
var DBG={};try{DBG=JSON.parse(localStorage.getItem('bf-debug')||'{}')}catch(e){}
var hr=history.replaceState,hp=history.pushState;
if(!DBG.nopretty)try{hr.call(history,history.state,'',pretty(location.href));}catch(e){}
function hpatch(f){return function(s,t,u){if(u!=null){var pu=pretty(String(u));try{VBASE=vbaseFrom(new URL(pu,location.href).pathname);}catch(e){}u=pu;}return f.call(this,s,t,u);};}
if(!DBG.nopretty){history.pushState=hpatch(hp);history.replaceState=hpatch(hr);}
/* address bar shows NullCash names for in-app hash routes (the app still sees its own on load) */
try{if(/^#nullswap\b/i.test(location.hash))hr.call(history,history.state,'',location.pathname+location.search+location.hash.replace(/^#nullswap/i,'#veilswap'));}catch(e){}
function hashBrand(){try{var h=location.hash||'';if(/veil/i.test(h))hr.call(history,history.state,'',location.pathname+location.search+h.replace(/veil/ig,'null'));}catch(e){}}
/* before the app's own handlers run (registered first), give the app its real hash back */
function hashUnbrand(){try{if(/^#nullswap\b/i.test(location.hash||''))hr.call(history,history.state,'',location.pathname+location.search+location.hash.replace(/^#nullswap/i,'#veilswap'));}catch(e){}}
window.addEventListener('popstate',hashUnbrand,true);
window.addEventListener('hashchange',hashUnbrand,true);
/* show the NullCash name only once the Bridge view is really on screen (app has finished routing) */
function bridgeOn(){var n=document.querySelectorAll('nav.side .nav-i.on');for(var i=0;i<n.length;i++){if(/^\s*Bridge\s*$/i.test(n[i].textContent||''))return true;}return false;}
var HBon=0;setInterval(function(){if(!/^#veilswap\b/i.test(location.hash||'')){HBon=0;return;}if(bridgeOn()){HBon++;if(HBon>=2)hashBrand();}else HBon=0;},400);
var of=window.fetch;
var HEAL=null,DISM=0;
function probe(){return of.call(window,R+'_health?_h='+Date.now(),{cache:'no-store',credentials:'same-origin'}).then(function(r){return r.ok&&(r.headers.get('content-type')||'').indexOf('json')>=0;},function(){return false;});}
function silent(){return new Promise(function(res){var f=document.createElement('iframe');f.setAttribute('aria-hidden','true');f.tabIndex=-1;f.style.cssText='position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;border:0;left:-9px;top:-9px';var fin=0,done=function(){if(fin)return;fin=1;try{f.remove();}catch(e){}res();};f.onload=function(){setTimeout(done,1200);};setTimeout(done,4000);f.src=R+'_health?_h='+Date.now();(document.body||document.documentElement).appendChild(f);});}
function ask(){return new Promise(function(res){
 var w=null,t=null,box=document.createElement('div');box.id='cb-verify';box.setAttribute('data-testid','cb-verify');
 box.innerHTML='<div class="cbv-card" role="dialog" aria-modal="true" aria-label="Quick security check"><div class="cbv-h"><b>Quick security check</b><button class="cbv-x" data-testid="cb-verify-close" aria-label="Close">&times;</button></div><p>Our network wants to confirm you are human before sending this request. Tap <b>Verify</b>, tick the box in the small window, and we will carry on automatically. Nothing is sent or charged until then.</p><button class="cbv-go" data-testid="cb-verify-go">Verify</button><div class="cbv-s" data-testid="cb-verify-status"></div></div>';
 var st=document.createElement('style');st.textContent='#cb-verify{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(4,3,8,.62);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px)}#cb-verify .cbv-card{width:min(420px,100%);background:#111016;border:1px solid #2e2d36;border-radius:6px;padding:20px;color:#e9e8ee;font:14px/1.5 system-ui,sans-serif}#cb-verify .cbv-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:16px}#cb-verify .cbv-x{background:none;border:0;color:#9b9aa3;font-size:22px;cursor:pointer}#cb-verify p{color:#a9a8b2;margin:0 0 16px}#cb-verify .cbv-go{width:100%;padding:12px;border:0;border-radius:3px;background:#fff;color:#0b0a10;font-weight:600;cursor:pointer}#cb-verify .cbv-s{margin-top:10px;font-size:12px;color:#8c8c96;min-height:16px}';
 box.appendChild(st);document.body.appendChild(box);
 function end(){clearInterval(t);try{w&&w.close();}catch(e){}box.remove();document.removeEventListener('keydown',kd,true);res();}
 function kd(e){if(e.key==='Escape'){e.stopPropagation();DISM=Date.now();end();}}
 document.addEventListener('keydown',kd,true);
 box.querySelector('.cbv-x').onclick=function(){DISM=Date.now();end();};
 box.querySelector('.cbv-go').onclick=function(){
  w=window.open(R+'_health?verify='+Date.now(),'cbverify','width=460,height=620');
  box.querySelector('.cbv-s').textContent='Waiting for the check to finish...';
  clearInterval(t);var n=0;t=setInterval(function(){n++;var ok=false;try{ok=w&&w.document&&w.document.body&&(w.document.body.innerText||'').trim().charAt(0)==='{';}catch(e){}
   if(ok||n%3===0)probe().then(function(p){if(p||ok)end();});
   if(n>300)end();},700);
 };
});}
function heal(){if(HEAL)return HEAL;HEAL=probe().then(function(ok){if(ok)return;return silent().then(probe).then(function(ok2){if(!ok2&&Date.now()-DISM>60000)return ask();});}).then(function(){setTimeout(function(){HEAL=null;},1500);});return HEAL;}
window.__bfHeal=heal;
function isChal(r){var ct=(r.headers.get('content-type')||'');return (r.status===403||r.status===503||r.status===429)&&ct.indexOf('text/html')>=0||(r.headers.get('cf-mitigated')==='challenge');}
function same(u){try{var x=new URL(u,location.href);return x.origin===location.origin&&x.pathname.indexOf('/api/')===0;}catch(e){return false;}}
function hf(i,o){var u=typeof i==='string'?i:(i&&i.url)||'';var replay=!(i&&i.url&&typeof i!=='string'&&i.body)&&!(o&&o.body&&typeof o.body!=='string');
 return of.call(window,i,o).then(function(r){if(!same(u)||!replay||!isChal(r))return r;return heal().then(function(){return of.call(window,i,o);});});}
window.__bfFetch=of&&hf;if(of){window.fetch=function(i,o){try{
 if(typeof i==='string')i=fx(i);
 else if(i instanceof URL)i=fx(i.href);
 else if(i&&i.url){var n=fx(i.url);if(n!==i.url)i=new Request(n,i);}
}catch(e){}return hf(i,o);};}
var xo=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){
 arguments[1]=fx(typeof u==='string'?u:String(u));return xo.apply(this,arguments);};
var IMGMAP=[[/veil-lockup/i,BU+'lockup.svg'],[/veil-mark|ext-mark|\/icons\/icon-\d+|\/icons\/apple|veil-wallet|\/wallets\/veil/i,BU+'mark.png'],[/favicon|apple-touch-icon/i,BU+'favicon.png']];
function bimg(v){if(typeof v!=='string')return null;if(/veil-lockup|_brand\/lockup/i.test(v))return LK();for(var i=0;i<IMGMAP.length;i++){if(IMGMAP[i][0].test(v))return IMGMAP[i][1];}return null;}
var AT={src:1,href:1,srcset:1,action:1,poster:1,'xlink:href':1};
function fs(v){return String(v).split(',').map(function(p){var t=p.trim();var i=t.indexOf(' ');var u=i<0?t:t.slice(0,i);return (p.match(/^\s*/)[0])+fx(u)+(i<0?'':t.slice(i));}).join(',');}
function mapAttr(el,k,v){
 if(k==='src'&&el&&el.tagName==='IMG'){var b=bimg(v);if(b)return b;}
 return k==='srcset'?fs(v):fx(v);
}
var sa=Element.prototype.setAttribute;
Element.prototype.setAttribute=function(n,v){try{var k=String(n).toLowerCase();
 if(AT[k]&&typeof v==='string'){v=mapAttr(this,k,v);}}catch(e){}return sa.call(this,n,v);};
[[window.HTMLImageElement,'src'],[window.HTMLImageElement,'srcset'],[window.HTMLScriptElement,'src'],[window.HTMLLinkElement,'href'],
 [window.HTMLSourceElement,'src'],[window.HTMLSourceElement,'srcset'],[window.HTMLIFrameElement,'src'],[window.HTMLMediaElement,'src'],
 [window.HTMLFormElement,'action'],[window.HTMLAnchorElement,'href']].forEach(function(c){try{var pr=c[0].prototype;
 var d=Object.getOwnPropertyDescriptor(pr,c[1]);if(!d||!d.set)return;
 Object.defineProperty(pr,c[1],{configurable:true,enumerable:d.enumerable,get:d.get,
  set:function(v){d.set.call(this,mapAttr(this,c[1],String(v)));}});}catch(e){}});
try{var W=window.Worker;window.Worker=function(u,o){return new W(fx(typeof u==='string'?u:String(u)),o);};window.Worker.prototype=W.prototype;}catch(e){}
try{var SW=window.SharedWorker;if(SW){window.SharedWorker=function(u,o){return new SW(fx(typeof u==='string'?u:String(u)),o);};window.SharedWorker.prototype=SW.prototype;}}catch(e){}
try{if(navigator.serviceWorker){navigator.serviceWorker.register=function(){return new Promise(function(){});};}}catch(e){}
var es=window.EventSource;if(es){window.EventSource=function(u,o){return new es(fx(String(u)),o);};window.EventSource.prototype=es.prototype;}
var WS=window.WebSocket;window.WebSocket=function(u,p){try{var x=new URL(String(u),location.href);
 if(x.host===location.host&&x.pathname.indexOf(R)!==0){x.pathname=up(x.pathname);}u=x.href;}catch(e){}
 return p===undefined?new WS(u):new WS(u,p);};
window.WebSocket.prototype=WS.prototype;['CONNECTING','OPEN','CLOSING','CLOSED'].forEach(function(k){window.WebSocket[k]=WS[k];});
var wo=window.open;window.open=function(u){if(typeof u==='string')arguments[0]=fx(u);return wo.apply(this,arguments);};
document.addEventListener('click',function(e){var a=e.target&&e.target.closest&&e.target.closest('a[href]');
 if(!a)return;var h=a.getAttribute('href');if(!h||SKIP.test(h))return;var n=fx(h);if(n!==h)a.setAttribute('href',n);},true);

/* ---------- NullCash branding ---------- */
var TXT=/veilit\.xyz|Veil|ContractWolf|Jupiter|veil-core|@veil|NEAR Intents|NEAR intents|1Click|DarkSwap|Darkswap|Dark Swap|HoudiniSwap|Houdini|Mayan|Cadabra|Darkinator|Bifrost|(^|[^$A-Za-z0-9_\/.:@-])veil(?![A-Za-z0-9_-])/;
function rt(s){
 return s.replace(/app\.veilit\.xyz/g,location.host+'/shield')
  .replace(/(?:www\.)?veilit\.xyz/g,location.host)
  .replace(/Audited by ContractWolf/g,'Independently audited')
  .replace(/External audit · ContractWolf/g,'External audit')
  .replace(/its audit by ContractWolf/g,'its independent audit')
  .replace(/ContractWolf · /g,'Independent audit · ')
  .replace(/ContractWolf/g,'an independent auditor')
  .replace(/veil-core/g,BRAND.toLowerCase()+'-core')
  .replace(/@veil[A-Za-z0-9_]*/g,'@usenullcash')
  .replace(/NEAR [Ii]ntents( 1Click)?/g,'Intent network').replace(/1Click/g,'Intent')
  .replace(/HoudiniSwap|Houdini/g,'Private route').replace(/Dark ?[Ss]wap|Darkinator|Cadabra|Bifrost/g,BRAND).replace(/Mayan/g,BRAND)
  .replace(/Jupiter Lend Earn/g,'Lending vault').replace(/Jupiter/g,'Aggregator')
  .replace(/(^|[^$A-Za-z])Veil/g,'$1'+BRAND)
  .replace(/(^|[^$A-Za-z0-9_\/.:@-])veil(?![A-Za-z0-9_-])/g,'$1'+BRAND.toLowerCase());
}
var SKIPTAG={SCRIPT:1,STYLE:1,NOSCRIPT:1,TEXTAREA:1,INPUT:1,TEMPLATE:1};
var BATTR=['title','aria-label','placeholder','alt','data-tip','data-title'];
function brandEl(el){
 if(DBG.noel)return;
 if(el.tagName==='META'||el.tagName==='LINK'||el.tagName==='TITLE'||el.tagName==='HEAD')return;
 for(var i=0;i<BATTR.length;i++){var v=el.getAttribute&&el.getAttribute(BATTR[i]);if(v&&TXT.test(v)){var nv=rt(v);if(nv!==v)sa.call(el,BATTR[i],nv);}}
 if(el.tagName==='IMG'){
  var s=el.getAttribute('src')||'';var b=bimg(s);
  if(!b&&el.closest&&el.closest('a.brand,.brand'))b=LK();
  if(!b&&/^(veil|darkinator|nullcash)$/i.test(el.getAttribute('alt')||''))b=LK();
  if(b&&s!==b&&s.indexOf(b)<0){sa.call(el,'src',b);el.removeAttribute('srcset');}
 }
 if(el.tagName==='A'){var hh=el.getAttribute('href')||'';if(/(x|twitter)\.com\/veil/i.test(hh))sa.call(el,'href','https://x.com/usenullcash');}
}
var PARA=__PARA__;
function brandText(n){if(DBG.notext)return;if(n.parentNode&&(SKIPTAG[n.parentNode.nodeName]||n.parentNode.nodeName==='TITLE'))return;var v=n.nodeValue;if(!v)return;var r=TXT.test(v)?rt(v):v;var tr=r.trim();if(tr&&PARA.hasOwnProperty(tr)){r=r.replace(tr,PARA[tr]);}if(r!==v)n.nodeValue=r;}
function walk(root){
 if(!root)return;
 if(root.nodeType===3){brandText(root);return;}
 if(root.nodeType!==1&&root.nodeType!==9)return;
 if(root.nodeType===1){if(SKIPTAG[root.nodeName])return;brandEl(root);}
 var w=document.createTreeWalker(root,5,null),n;
 while((n=w.nextNode())){if(n.nodeType===3)brandText(n);else if(!SKIPTAG[n.nodeName])brandEl(n);}
}
var started=false;
function start(){
 if(started)return;started=true;
 if(DBG.nobrand){document.documentElement.classList.add('bf-ready');setInterval(function(){document.documentElement.classList.add('bf-ready');},300);return;}
 walk(document.documentElement);
 try{var fl=document.createElement('link');sa.call(fl,'rel','icon');sa.call(fl,'href',BU+'favicon.png?v=4');sa.call(fl,'id','bf-icon');document.head.appendChild(fl);}catch(e){}
 var syncTitle=function(){var t=document.title;if(t&&TXT.test(t)){var nt=rt(t);if(nt!==t)document.title=nt;}};
 syncTitle();setInterval(syncTitle,800);
 var CNT=0;window.__bft={};
 var mo=new MutationObserver(function(ms){if(DBG.trace){CNT+=ms.length;for(var z=0;z<Math.min(ms.length,20);z++){var mz=ms[z];var kk=mz.type+'|'+(mz.attributeName||'')+'|'+(mz.target.nodeName||'')+'|'+String(mz.target.nodeValue||(mz.target.getAttribute&&mz.target.getAttribute(mz.attributeName||'class'))||'').slice(0,50);window.__bft[kk]=(window.__bft[kk]||0)+1;}if(CNT>20000){mo.disconnect();localStorage.setItem('bf-trace',JSON.stringify(Object.entries(window.__bft).sort(function(a,b){return b[1]-a[1]}).slice(0,15)));return;}}
 QQ.push.apply(QQ,ms);if(!QS){QS=1;(window.requestAnimationFrame||setTimeout)(flushQ);}});
 var QQ=[],QS=0;function flushQ(){QS=0;var ms=QQ;QQ=[];
 for(var i=0;i<ms.length;i++){var m=ms[i];
  if(m.type==='characterData')brandText(m.target);
  else if(m.type==='attributes'){if(m.attributeName==='data-theme'){var im=document.images;for(var k=0;k<im.length;k++)brandEl(im[k]);}else brandEl(m.target);}
  else for(var j=0;j<m.addedNodes.length;j++)walk(m.addedNodes[j]);}}
 mo.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['src','href','title','aria-label','placeholder','alt','content','data-theme']});
 document.documentElement.classList.add('bf-ready');
 // React may re-render <html> after a hydration mismatch and drop our class: keep the page revealed
 setInterval(function(){var de=document.documentElement;if(!de.classList.contains('bf-ready'))de.classList.add('bf-ready');},200);
}
if(T==='site'){
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
}else{
 // reveal as soon as the DOM is parsed (do not wait for every image/font on slow mobile links)
 var go=function(){setTimeout(start,30);};
 if(document.readyState!=='loading')go();else document.addEventListener('DOMContentLoaded',go);
}
setTimeout(start,2500);
})();
"""


NAV_LABELS = {
    "Home": "Home", "Shield": "Shield", "Trade": "Swap", "NullCashSwap": "Bridge",
    "Send": "Send", "Receive": "Receive", "Withdraw": "Withdraw", "Earn": "Earn",
    "Invite": "Invite", "Addresses": "Addresses", "Activity": "History", "Keys": "Keys",
    "More": "More",
}


@functools.lru_cache(maxsize=1)
def _para_json() -> str:
    import json as _j
    try:
        d = _j.loads((BRAND_DIR / "paraphrase.json").read_text())
    except Exception:
        d = {}
    d.update(NAV_LABELS)
    return _j.dumps(d, ensure_ascii=False).replace("</", "<\\/")


@functools.lru_cache(maxsize=8)
def head_inject(target: str, prefix: str) -> str:
    import json as _j
    js = (_SHIM.replace("__P__", f"{prefix}/{target}")
          .replace("__R__", f"{prefix}/")
          .replace("__T__", target)
          .replace("__BRAND__", BRAND_NAME)
          .replace("__PARA__", _para_json()))
    try:
        theme_css = (BRAND_DIR / "nc-theme.css").read_text()
    except Exception:
        theme_css = ""
    css_boot = ("(function(){var s=document.createElement('style');s.id='cb-style';s.textContent="
                + _j.dumps(BRAND_CSS + "\n" + theme_css) + ";(document.head||document.documentElement).appendChild(s);})();")
    payload = base64.b64encode((css_boot + js).encode("utf-8")).decode()
    # the shim ships encoded so page source shows no upstream identifiers
    boot = ("(0,eval)(new TextDecoder().decode(Uint8Array.from(atob('" + payload
            + "'),function(c){return c.charCodeAt(0)})))")
    return (
        f"<script>{boot}</script>"
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap">'
        + (f'<script src="{prefix}/_brand/dk-swap.js" defer></script>' if target == "app" else "")
    )


_ICON_V = "4"


def nullcash_manifest():
    b = "/api/bifrost/v1/_brand/"
    return {
        "name": "NullCash · Private money on Solana",
        "short_name": "NullCash",
        "description": "Private balances, transfers, trades and swaps on Solana.",
        "id": "/app",
        "start_url": "/app",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#ffffff",
        "icons": [
            {"src": f"{b}favicon-192.png?v={_ICON_V}", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{b}favicon-512.png?v={_ICON_V}", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{b}maskable-512.png?v={_ICON_V}", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


def brand_icon_for(path: str):
    """Map any upstream favicon / app-icon / manifest path to a NullCash brand asset name."""
    p = path.lower().split("?", 1)[0]
    base = p.rsplit("/", 1)[-1]
    if base in ("manifest.webmanifest", "manifest.json", "site.webmanifest"):
        return "manifest.webmanifest"
    if base == "favicon.ico":
        return "favicon.ico"
    if base.startswith("apple-touch-icon"):
        return "apple-touch-icon.png"
    if base.startswith("maskable"):
        return "maskable-512.png"
    if base.startswith(("favicon", "android-chrome", "icon-")) and base.endswith((".png", ".svg", ".webp", ".ico")):
        import re as _re
        m = _re.search(r"(\d+)", base)
        n = int(m.group(1)) if m else 64
        return "favicon-32.png" if n <= 32 else "favicon-64.png" if n <= 64 else "favicon-192.png" if n <= 192 else "favicon-512.png"
    return None


def brand_asset(name: str):
    """Return (bytes, content_type) or None."""
    if name == "lockup.svg":
        return lockup_svg().encode(), "image/svg+xml"
    if name == "lockup-light.svg":
        return lockup_svg(True).encode(), "image/svg+xml"
    if name == "dk-swap.js":
        try:
            return (BRAND_DIR / "dk-swap.js").read_bytes(), "application/javascript; charset=utf-8"
        except Exception:
            return None
    if name == "mark.png":
        p = BRAND_DIR / "mark-256.png"
    elif name == "mark-black.png":
        p = BRAND_DIR / "mark-black-256.png"
    elif name == "mark-white.png":
        p = BRAND_DIR / "mark-white-256.png"
    elif name == "favicon.png":
        p = BRAND_DIR / "favicon-192.png"
    elif name == "favicon.ico":
        try:
            return (BRAND_DIR / "favicon.ico").read_bytes(), "image/x-icon"
        except Exception:
            return None
    elif name in ("favicon-32.png", "favicon-64.png", "favicon-192.png", "favicon-512.png",
                  "apple-touch-icon.png", "maskable-512.png"):
        p = BRAND_DIR / name
    elif name == "manifest.webmanifest":
        import json as _json
        return _json.dumps(nullcash_manifest()).encode(), "application/manifest+json"
    else:
        return None
    try:
        return p.read_bytes(), "image/png"
    except Exception:
        return None
