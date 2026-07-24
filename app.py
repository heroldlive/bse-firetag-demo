"""
BSE FireTag — self-contained demo (single file).
API + mobile UI + SQLite seed, for disposable phone testing on Render.
NOT production: no auth, SQLite resets on each cold start. Demo only.
"""
from flask import Flask, request, jsonify, Response
from datetime import date
import sqlite3, os, csv, io

DB = "/tmp/firetag.db"
app = Flask(__name__)

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c

def init():
    fresh = not os.path.exists(DB)
    c = conn()
    if fresh:
        c.executescript("""
        CREATE TABLE extinguisher_types(code TEXT PRIMARY KEY,label TEXT,recharge_years REAL,hydro_years INT,fire_classes TEXT);
        CREATE TABLE owners(id INTEGER PRIMARY KEY,entity_name TEXT NOT NULL,contact_person TEXT,contact_phone TEXT,contact_email TEXT,odoo_partner_id INT,notes TEXT);
        CREATE TABLE sites(id INTEGER PRIMARY KEY,owner_id INT REFERENCES owners(id),building_name TEXT NOT NULL,address TEXT);
        CREATE TABLE assets(id INTEGER PRIMARY KEY,site_id INT REFERENCES sites(id),type_code TEXT REFERENCES extinguisher_types(code),
            location_in_site TEXT,rating TEXT,manufacturer_serial TEXT,current_tag_serial TEXT,status TEXT DEFAULT 'active');
        CREATE TABLE tag_inventory(tag_serial TEXT PRIMARY KEY,consumed_at TEXT,consumed_event INT);
        CREATE TABLE service_events(id INTEGER PRIMARY KEY,asset_id INT REFERENCES assets(id),
            service_date TEXT NOT NULL,tag_serial TEXT NOT NULL,owner_name_at_service TEXT NOT NULL,
            work_inspected INT DEFAULT 0,work_serviced INT DEFAULT 0,work_recharged INT DEFAULT 0,work_hydrotested INT DEFAULT 0,
            competent_person TEXT NOT NULL,defects_notes TEXT,retired_tag_serial TEXT);
        CREATE VIEW register_10_3_3_1 AS
          SELECT e.service_date AS date_of_work,e.tag_serial AS tag_serial_affixed,e.owner_name_at_service AS owner_entity_name,
                 a.id AS asset_id,t.label AS extinguisher_type,s.building_name,a.location_in_site
          FROM service_events e JOIN assets a ON a.id=e.asset_id JOIN sites s ON s.id=a.site_id
          JOIN extinguisher_types t ON t.code=a.type_code ORDER BY e.service_date DESC;
        """)
        c.executemany("INSERT INTO extinguisher_types VALUES(?,?,?,?,?)",[
            ('water_sp','Water (stored pressure)',2.5,5,'A'),('water_gc','Water (gas cartridge)',5.0,5,'A'),
            ('chem_foam','Chemical foam',2.5,5,'A,B'),('mfoam_gc','Mechanical foam (gas cartridge)',2.5,5,'A,B'),
            ('mfoam_sp','Mechanical foam / AFFF (stored pressure)',2.5,5,'A,B'),('powder_gc','Powder (gas cartridge)',5.0,5,'A,B'),
            ('powder_sp','Powder (stored pressure)',5.0,5,'A,B'),('co2','Carbon dioxide',10.0,10,'B,electrical'),
            ('clean','Clean agent',10.0,10,'A,B,electrical'),('wet_chem','Wet chemical (Class F)',2.5,5,'F,A')])
        c.execute("INSERT INTO owners(entity_name,odoo_partner_id) VALUES(?,NULL)",("Pumps Service Centre Pte Ltd",))
        c.execute("INSERT INTO sites(owner_id,building_name,address) VALUES(1,?,?)",("Blk 5 Tuas Ave 2","Tuas"))
        c.execute("INSERT INTO assets(site_id,type_code,location_in_site,rating,manufacturer_serial,current_tag_serial) VALUES(1,?,?,?,?,?)",
                  ('powder_sp','Level 3 lift lobby','21A/144B','MFR-77120','PSB-39980'))
        c.execute("INSERT INTO assets(site_id,type_code,location_in_site,rating,manufacturer_serial,current_tag_serial) VALUES(1,?,?,?,?,?)",
                  ('co2','Server room','34B','MFR-77121','PSB-39981'))
        c.executemany("INSERT INTO tag_inventory(tag_serial) VALUES(?)",[('PSB-40021',),('PSB-40022',),('PSB-40023',),('PSB-40024',)])
        c.commit()
    c.close()

# ---------------- API ----------------
@app.get("/api/asset/by-tag/<path:s>")
def by_tag(s):
    c=conn(); r=c.execute("""SELECT a.*,t.label type_label,t.recharge_years,t.hydro_years,s.building_name,o.entity_name owner_name
        FROM assets a JOIN sites s ON s.id=a.site_id JOIN owners o ON o.id=s.owner_id JOIN extinguisher_types t ON t.code=a.type_code
        WHERE a.current_tag_serial=? AND a.status='active'""",(s,)).fetchone()
    if not r: c.close(); return jsonify(error="retired or unknown tag"),404
    return jsonify(_due(dict(r),c))

@app.get("/api/types")
def types():
    c=conn(); rows=[dict(x) for x in c.execute("SELECT * FROM extinguisher_types").fetchall()]; c.close()
    return jsonify(rows)

@app.post("/api/asset")
def create_asset():
    d=request.json; c=conn()
    try:
        tag=d.get("tag_serial")
        if tag:
            existing=c.execute("SELECT id FROM assets WHERE current_tag_serial=? AND status='active'",(tag,)).fetchone()
            if existing: return jsonify(error="tag already bound to an asset"),409
        cur=c.execute("""INSERT INTO assets(site_id,type_code,location_in_site,rating,manufacturer_serial,current_tag_serial)
            VALUES(1,?,?,?,?,?)""",(d["type_code"],d["location_in_site"],d.get("rating",""),d.get("manufacturer_serial",""),tag))
        c.commit()
        return jsonify(id=cur.lastrowid),201
    except Exception as e:
        c.rollback(); return jsonify(error=str(e)),500
    finally:
        c.close()

@app.get("/api/asset/<int:aid>")
def asset(aid):
    c=conn(); r=c.execute("""SELECT a.*,t.label type_label,t.recharge_years,t.hydro_years,s.building_name,o.entity_name owner_name
        FROM assets a JOIN sites s ON s.id=a.site_id JOIN owners o ON o.id=s.owner_id JOIN extinguisher_types t ON t.code=a.type_code
        WHERE a.id=?""",(aid,)).fetchone()
    if not r: c.close(); return jsonify(error="not found"),404
    return jsonify(_due(dict(r),c))

@app.get("/api/asset/<int:aid>/history")
def history(aid):
    c=conn(); rows=[dict(x) for x in c.execute("SELECT * FROM service_events WHERE asset_id=? ORDER BY service_date DESC",(aid,)).fetchall()]; c.close()
    return jsonify(rows)

@app.get("/api/asset/search")
def search():
    q=f"%{request.args.get('q','').lower()}%"; c=conn()
    rows=[dict(x) for x in c.execute("""SELECT a.id,a.location_in_site,a.manufacturer_serial,a.current_tag_serial,t.label type_label,
        s.building_name,o.entity_name owner_name FROM assets a JOIN sites s ON s.id=a.site_id JOIN owners o ON o.id=s.owner_id
        JOIN extinguisher_types t ON t.code=a.type_code WHERE a.status='active' AND (lower(o.entity_name) LIKE ? OR
        lower(s.building_name) LIKE ? OR lower(a.location_in_site) LIKE ? OR lower(a.manufacturer_serial) LIKE ?) LIMIT 25""",
        (q,q,q,q)).fetchall()]; c.close(); return jsonify(rows)

@app.post("/api/asset/<int:aid>/service")
def log_service(aid):
    d=request.json; new_tag=d["tag_serial"]; c=conn()
    try:
        r=c.execute("""SELECT o.entity_name,a.current_tag_serial FROM assets a JOIN sites s ON s.id=a.site_id
                       JOIN owners o ON o.id=s.owner_id WHERE a.id=?""",(aid,)).fetchone()
        if not r: return jsonify(error="asset not found"),404
        owner_name,old_tag=r["entity_name"],r["current_tag_serial"]
        tag=c.execute("SELECT consumed_at FROM tag_inventory WHERE tag_serial=?",(new_tag,)).fetchone()
        if not tag: return jsonify(error="tag not in issued inventory"),400
        if tag["consumed_at"] is not None: return jsonify(error="tag already consumed"),409
        cur=c.execute("""INSERT INTO service_events(asset_id,service_date,tag_serial,owner_name_at_service,work_inspected,
            work_serviced,work_recharged,work_hydrotested,competent_person,defects_notes,retired_tag_serial)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (aid,d.get("service_date",date.today().isoformat()),new_tag,owner_name,int(d.get("inspected",False)),
             int(d.get("serviced",False)),int(d.get("recharged",False)),int(d.get("hydrotested",False)),
             d["competent_person"],d.get("defects","None"),old_tag))
        eid=cur.lastrowid
        c.execute("UPDATE assets SET current_tag_serial=? WHERE id=?",(new_tag,aid))
        c.execute("UPDATE tag_inventory SET consumed_at=?,consumed_event=? WHERE tag_serial=?",(date.today().isoformat(),eid,new_tag))
        c.commit()
        return jsonify(event_id=eid,retired_tag=old_tag,current_tag=new_tag),201
    except Exception as e:
        c.rollback(); return jsonify(error=str(e)),500
    finally:
        c.close()

@app.get("/api/register")
def register():
    c=conn(); rows=[dict(x) for x in c.execute("SELECT * FROM register_10_3_3_1").fetchall()]; c.close()
    if request.args.get("format")=="csv":
        buf=io.StringIO(); w=csv.writer(buf)
        w.writerow(["Date of work","Tag serial affixed","Owner entity name","Asset ID","Type","Building","Location"])
        for r in rows: w.writerow([r["date_of_work"],r["tag_serial_affixed"],r["owner_entity_name"],r["asset_id"],
                                   r["extinguisher_type"],r["building_name"],r["location_in_site"]])
        return Response(buf.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=register_ss578.csv"})
    return jsonify(rows)

@app.get("/api/tags")
def tags():
    c=conn(); avail=[x["tag_serial"] for x in c.execute("SELECT tag_serial FROM tag_inventory WHERE consumed_at IS NULL").fetchall()]
    used=c.execute("SELECT count(*) n FROM tag_inventory WHERE consumed_at IS NOT NULL").fetchone()["n"]; c.close()
    return jsonify(available=avail,blank=len(avail),used=used)

@app.post("/api/reset")
def reset():
    if os.path.exists(DB): os.remove(DB)
    init(); return jsonify(ok=True)

def _due(a,c):
    a["last_service"]=c.execute("SELECT max(service_date) d FROM service_events WHERE asset_id=?",(a["id"],)).fetchone()["d"]
    c.close(); return a

# ---------------- UI (served at /) ----------------
@app.get("/")
def ui():
    return Response(PAGE, mimetype="text/html")

PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>BSE FireTag</title>
<style>
:root{--bg:#faf9f5;--card:#fff;--bd:#e5e2d9;--bd2:#d0cdc3;--tx:#1a1a18;--tx2:#6b6a63;--tx3:#9a988f;--acc:#185fa5;--accbg:#e6f1fb;--ok:#0f6e56;--okbg:#e1f5ee;--warn:#854f0b;--warnbg:#faeeda;--danger:#a32d2d;--dangerbg:#fcebeb;--mono:ui-monospace,Menlo,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:440px;margin:0 auto;min-height:100vh;display:flex;flex-direction:column}
.hdr{padding:14px 16px;border-bottom:.5px solid var(--bd);display:flex;justify-content:space-between;align-items:center;background:var(--card);position:sticky;top:0;z-index:2}
.hdr b{font-weight:600;font-size:15px}.inv{font-size:12px;color:var(--tx2)}
.sc{flex:1;padding:16px}
.muted{color:var(--tx2);font-size:13px}.tiny{color:var(--tx3);font-size:11px}
.mono{font-family:var(--mono)}
button{font-family:inherit}
.btn{width:100%;padding:12px 14px;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer;border:.5px solid var(--bd2);background:var(--card);color:var(--tx);text-align:left;margin-bottom:8px}
.btn:active{transform:scale(.99)}.btn.acc{border-color:var(--acc);background:var(--accbg);color:var(--acc);text-align:center}
.lnk{background:none;border:none;color:var(--acc);font-size:13px;cursor:pointer;padding:0}
.back{background:none;border:none;color:var(--tx2);font-size:13px;cursor:pointer;padding:0;margin-bottom:12px}
input{width:100%;padding:10px 12px;border:.5px solid var(--bd2);border-radius:8px;font-size:15px;margin:6px 0 14px;background:var(--card);color:var(--tx)}
.scan{border:2px dashed var(--bd2);border-radius:12px;height:140px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--tx3);margin-bottom:14px;position:relative;overflow:hidden;cursor:pointer;background:#000}
.scan video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.scan .hint{position:relative;z-index:1;color:#fff;text-shadow:0 1px 3px rgba(0,0,0,.6)}
.scan.idle{background:var(--card);color:var(--tx3)}
.scan.idle .hint{color:inherit;text-shadow:none}
.chip{display:inline-flex;align-items:center;font-size:13px;border:.5px solid var(--bd2);border-radius:20px;padding:7px 13px;margin:0 6px 8px 0;cursor:pointer}
.chip input{width:auto;margin:0 6px 0 0}
.row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:.5px solid var(--bd);font-size:13px}
.pill{font-size:11px;padding:2px 9px;border-radius:20px}
.card{border:.5px solid var(--bd2);border-radius:8px;padding:14px;font-size:12px;background:var(--card)}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:5px 4px;border-bottom:.5px solid var(--bd)}th{color:var(--tx2);font-weight:500}
.ok{color:var(--ok)}.big{font-size:22px;font-weight:600}
.svg{width:40px;height:40px}
</style></head><body><div class="wrap">
<div class="hdr"><b>🔥 BSE FireTag</b><span class="inv" id="inv"></span></div>
<div class="sc" id="sc"></div></div>
<script src="https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js"></script>
<script>
const A=(p,o)=>{
  const ctrl=new AbortController();const t=setTimeout(()=>ctrl.abort(),10000);
  return fetch(p,{...(o||{}),signal:ctrl.signal}).then(r=>r.json().then(j=>({ok:r.ok,status:r.status,j})))
    .catch(err=>({ok:false,status:0,j:{error:err.name==='AbortError'?'Request timed out':(err.message||'Network error')}}))
    .finally(()=>clearTimeout(t));
};
let V={s:'scan',aid:null,pend:null,asset:null,hist:[]};
let CAM={stream:null,raf:null};
function stopCam(){if(CAM.raf)cancelAnimationFrame(CAM.raf);if(CAM.stream)CAM.stream.getTracks().forEach(t=>t.stop());CAM.stream=null;CAM.raf=null;}
async function startCam(){
  const box=document.getElementById('viewfinder');if(!box)return;
  try{
    const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
    CAM.stream=stream;
    box.classList.remove('idle');
    box.innerHTML='<video id="camv" autoplay playsinline muted></video><span class="hint" style="font-size:12px">Point at tag QR code</span>';
    const video=document.getElementById('camv');video.srcObject=stream;await video.play();
    const SCAN_W=360;let scanH=0,sized=false;
    const canvas=document.createElement('canvas');const ctx=canvas.getContext('2d',{willReadFrequently:true});
    let lastTick=0;
    const tick=now=>{
      if(!CAM.stream)return;
      if(video.readyState===video.HAVE_ENOUGH_DATA){
        if(!sized&&video.videoWidth){scanH=Math.round(SCAN_W*video.videoHeight/video.videoWidth);canvas.width=SCAN_W;canvas.height=scanH;sized=true;}
        if(sized&&(!lastTick||now-lastTick>150)){
          lastTick=now;
          ctx.drawImage(video,0,0,SCAN_W,scanH);
          const img=ctx.getImageData(0,0,SCAN_W,scanH);
          const code=window.jsQR&&jsQR(img.data,img.width,img.height);
          if(code&&code.data){const val=code.data.trim();stopCam();scan(val);return;}
        }
      }
      CAM.raf=requestAnimationFrame(tick);
    };
    CAM.raf=requestAnimationFrame(tick);
  }catch(err){
    box.classList.add('idle');
    box.innerHTML='<div style="font-size:32px">▦</div><span class="hint" style="font-size:12px">Camera unavailable — '+(err.message||'permission denied')+'</span>';
  }
}
const sc=()=>document.getElementById('sc');
async function inv(){const {j}=await A('/api/tags');document.getElementById('inv').textContent='🎫 '+j.blank+' blank';}
function due(a,y){return a.last_service?('+'+y+'y from '+a.last_service):'after first service';}

async function render(){
  await inv(); const e=sc();
  if(V.s==='scan'){
    e.innerHTML=`<p class="muted"><b>Scan control tag</b> to identify the extinguisher.</p>
    <div class="scan idle" id="viewfinder" onclick="startCam()"><div style="font-size:40px">▦</div><span class="hint" style="font-size:12px">Tap to open camera</span></div>
    <p class="tiny" style="margin-bottom:6px">Demo — tap an affixed tag:</p><div id="tg"></div>
    <button class="lnk" onclick="go('find')">🔍 Can't scan? Find by location</button>
    <div style="margin-top:16px"><button class="lnk" onclick="showReg()">View §10.3.3.1 register →</button></div>
    <div style="margin-top:10px"><button class="lnk" onclick="resetDemo()" style="color:var(--tx3)">↺ Reset demo data</button></div>`;
    const a1=await A('/api/asset/1'),a2=await A('/api/asset/2');
    document.getElementById('tg').innerHTML=[a1.j,a2.j].filter(x=>x.current_tag_serial).map(x=>`<button class="btn" onclick="scan('${x.current_tag_serial}')"><span class="mono">${x.current_tag_serial}</span> · ${x.location_in_site}</button>`).join('');
  }
  else if(V.s==='find'){
    e.innerHTML=`<button class="back" onclick="go('scan')">← Scan</button><p style="font-weight:500;margin:0 0 8px">Find by location</p>
    <input id="q" placeholder="site, building, location, or mfr serial" oninput="rfind()"><div id="res"></div>`;
    rfind();
  }
  else if(V.s==='asset'){
    const a=V.asset;const h=V.hist;
    const badge=(txt,cls)=>`<span class="pill" style="background:var(--${cls}bg);color:var(--${cls})">${txt}</span>`;
    e.innerHTML=`<button class="back" onclick="go('scan')">← Scan</button>
    <div class="big">Asset ${a.id}</div>
    <p class="muted" style="margin:2px 0 3px">${a.type_label} · ${a.location_in_site}</p>
    <p class="tiny" style="margin:0 0 3px">${a.owner_name} · ${a.building_name}</p>
    <p class="tiny" style="margin:0 0 12px">Current tag: <span class="mono">${a.current_tag_serial||'— none —'}</span> · mfr ${a.manufacturer_serial}</p>
    <div class="row"><span class="muted">Maintenance</span><span>${a.last_service?'12-month cycle':'annual'} ${badge(a.last_service?'OK':'Due','ok')}</span></div>
    <div class="row"><span class="muted">Recharge</span><span>${a.recharge_years}y · ${due(a,a.recharge_years)}</span></div>
    <div class="row"><span class="muted">Hydrostatic</span><span>${a.hydro_years}y · ${due(a,a.hydro_years)}</span></div>
    <p style="font-weight:500;margin:14px 0 6px">History</p>
    ${h.length?h.map(x=>`<div style="font-size:12px;padding:5px 0;border-bottom:.5px solid var(--bd)">${x.service_date} — ${[x.work_inspected&&'Inspected',x.work_serviced&&'Serviced',x.work_recharged&&'Recharged',x.work_hydrotested&&'Hydro-tested'].filter(Boolean).join(', ')} · ${x.competent_person}<br><span class="tiny">tag ${x.tag_serial} · ${x.defects_notes}</span></div>`).join(''):'<p class="tiny">No services yet.</p>'}
    <button class="btn acc" style="margin-top:14px" onclick="go('svc')">+ Renew / log service</button>`;
  }
  else if(V.s==='svc'){
    e.innerHTML=`<button class="back" onclick="openAsset(V.aid)">← Asset</button><p style="font-weight:500;margin:0 0 10px">Log service — 1 of 2</p>
    <div id="wk" style="margin-bottom:12px">${['Inspected','Serviced','Recharged','Hydro-tested'].map(w=>`<label class="chip"><input type="checkbox" value="${w}">${w}</label>`).join('')}</div>
    <label class="muted">Competent person</label><input id="cp" value="Kumar R.">
    <label class="muted">Defects (Annex B)</label><input id="df" placeholder="None">
    <button class="btn acc" onclick="toTag()">Next: swap tag →</button>`;
  }
  else if(V.s==='tag'){
    const a=V.asset;const {j}=await A('/api/tags');
    e.innerHTML=`<button class="back" onclick="go('svc')">← Details</button><p style="font-weight:500;margin:0 0 3px">Swap tag — 2 of 2</p>
    <p class="tiny" style="margin:0 0 12px">Retiring <span class="mono">${a.current_tag_serial||'none'}</span>, binding a new blank tag.</p>
    ${j.available.length?j.available.map(t=>`<button class="btn mono" onclick="post('${t}')">${t}</button>`).join(''):'<p style="color:var(--danger);font-size:13px">No blank tags — reorder.</p>'}`;
  }
  else if(V.s==='done'){
    const r=V.pend;
    e.innerHTML=`<div style="text-align:center;margin-bottom:12px"><div style="font-size:38px" class="ok">✓</div><p style="font-size:13px;margin:6px 0 0">Committed · tag swapped · inventory decremented</p></div>
    <div class="card"><p style="text-align:center;font-weight:600;font-size:13px;margin:0 0 8px">B.S. ENGINEERING CO. PTE. LTD.</p>
    <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span class="muted">Date</span><span>${r.date}</span></div>
    <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span class="muted">Work</span><span>${r.work}</span></div>
    <div style="display:flex;justify-content:space-between;margin-bottom:8px"><span class="muted">By</span><span>${r.by}</span></div>
    <div style="border-top:.5px solid var(--bd);padding-top:8px;display:flex;justify-content:space-between;align-items:center"><div><span class="muted">Asset</span> ${V.aid} · <span class="muted">tag</span> <span class="mono">${r.tag}</span></div><span style="font-size:30px">▦</span></div></div>
    <div style="display:flex;gap:8px;margin-top:12px"><button class="btn" style="text-align:center" onclick="go('scan')">Next unit</button><button class="btn acc" onclick="showReg()">View register</button></div>`;
  }
  else if(V.s==='notfound'){
    e.innerHTML=`<button class="back" onclick="go('scan')">← Scan</button>
    <div style="text-align:center;margin:20px 0"><div style="font-size:38px">❓</div>
    <p style="font-weight:500;margin:10px 0 3px">Tag not recognized</p>
    <p class="tiny mono">${V.unknownTag}</p></div>
    <p class="muted" style="margin-bottom:14px">This tag isn't bound to any asset. Register a new extinguisher and bind it now?</p>
    <button class="btn acc" style="text-align:center" onclick="go('register')">+ Register new asset</button>
    <button class="lnk" style="margin-top:10px" onclick="go('find')">🔍 Find existing asset instead</button>`;
  }
  else if(V.s==='register'){
    const {j:types}=await A('/api/types');
    e.innerHTML=`<button class="back" onclick="go('notfound')">← Back</button><p style="font-weight:500;margin:0 0 3px">Register new asset</p>
    <p class="tiny" style="margin:0 0 12px">Binding tag <span class="mono">${V.unknownTag}</span></p>
    <label class="muted">Extinguisher type</label>
    <select id="rtype" style="width:100%;padding:10px 12px;border:.5px solid var(--bd2);border-radius:8px;font-size:15px;margin:6px 0 14px;background:var(--card);color:var(--tx)">
      ${types.map(t=>`<option value="${t.code}">${t.label}</option>`).join('')}
    </select>
    <label class="muted">Location in site</label><input id="rloc" placeholder="e.g. Level 3 lift lobby">
    <label class="muted">Rating</label><input id="rrating" placeholder="e.g. 21A/144B">
    <label class="muted">Manufacturer serial</label><input id="rmfr" placeholder="e.g. MFR-77120">
    <button class="btn acc" style="text-align:center" onclick="submitRegister()">Register & bind tag</button>`;
  }
  else if(V.s==='reg'){
    const {j}=await A('/api/register');
    e.innerHTML=`<button class="back" onclick="go('scan')">← Back</button><p style="font-weight:500;margin:0 0 3px">§10.3.3.1 register</p>
    <p class="tiny" style="margin:0 0 10px">The three mandated fields + operational context.</p>
    <div style="overflow-x:auto"><table><tr><th>Date</th><th>Tag</th><th>Owner entity</th></tr>
    ${j.length?j.map(x=>`<tr><td>${x.date_of_work}</td><td class="mono">${x.tag_serial_affixed}</td><td>${x.owner_entity_name}</td></tr>`).join(''):'<tr><td colspan=3 class="tiny">Empty — log a service.</td></tr>'}</table></div>
    <p class="tiny" style="margin:10px 0 0">Owner name snapshotted at service time — immutable if later renamed.</p>
    <a class="btn" style="text-align:center;display:block;text-decoration:none;margin-top:12px" href="/api/register?format=csv">⬇ Export CSV</a>`;
  }
}
window.go=s=>{stopCam();V.s=s;render()};
window.scan=async s=>{
  stopCam();
  const box=document.getElementById('viewfinder');
  if(box)box.outerHTML='<div class="scan idle" id="viewfinder"><div style="font-size:32px">⏳</div><span class="hint" style="font-size:12px">Looking up tag…</span></div>';
  const {ok,status,j}=await A('/api/asset/by-tag/'+encodeURIComponent(s));
  if(!ok&&status===0){alert('Lookup failed: '+(j.error||'network error')+'. Try again.');go('scan');return;}
  if(!ok){V.unknownTag=s;V.s='notfound';render();return;}
  V.asset=j;V.aid=j.id;const h=await A('/api/asset/'+j.id+'/history');V.hist=h.j;V.s='asset';render();
};
window.submitRegister=async()=>{
  const body={type_code:document.getElementById('rtype').value,location_in_site:document.getElementById('rloc').value||'—',
    rating:document.getElementById('rrating').value,manufacturer_serial:document.getElementById('rmfr').value,tag_serial:V.unknownTag};
  const {ok,status,j}=await A('/api/asset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!ok){alert('Error '+status+': '+(j.error||''));return;}
  openAsset(j.id);
};
window.openAsset=async id=>{const {j}=await A('/api/asset/'+id);V.asset=j;V.aid=id;const h=await A('/api/asset/'+id+'/history');V.hist=h.j;V.s='asset';render();};
window.rfind=async()=>{const q=document.getElementById('q').value;const {j}=await A('/api/asset/search?q='+encodeURIComponent(q));
  document.getElementById('res').innerHTML=j.length?j.map(a=>`<button class="btn" onclick="openAsset(${a.id})">Asset ${a.id} · ${a.location_in_site}<br><span class="tiny">${a.type_label} · ${a.building_name}</span></button>`).join(''):'<p class="tiny">No match.</p>';};
window.toTag=()=>{const w=[...document.querySelectorAll('#wk input:checked')].map(c=>c.value);if(!w.length){alert('Select work type');return;}
  V.pend={workArr:w,work:w.join(' / '),by:document.getElementById('cp').value||'—',def:document.getElementById('df').value||'None'};V.s='tag';render();};
window.post=async t=>{const p=V.pend;
  const body={competent_person:p.by,defects:p.def,tag_serial:t,service_date:'2026-07-20',
    inspected:p.workArr.includes('Inspected'),serviced:p.workArr.includes('Serviced'),
    recharged:p.workArr.includes('Recharged'),hydrotested:p.workArr.includes('Hydro-tested')};
  const {ok,status,j}=await A('/api/asset/'+V.aid+'/service',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!ok){alert('Error '+status+': '+(j.error||''));return;}
  V.pend={date:'2026-07-20',work:p.work,by:p.by,tag:t};V.s='done';render();};
window.showReg=()=>{V.s='reg';render()};
window.resetDemo=async()=>{await A('/api/reset',{method:'POST'});alert('Demo data reset');go('scan');};
render();
</script></body></html>"""

init()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
