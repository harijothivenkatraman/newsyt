"""
dashboard/app.py
Flask web dashboard for the YouTube News Bot.
View pipeline status, trigger runs, review articles, manage config.
Supports YouTube login/logout for channel switching.
Queue-aware: shows pending article queue, enqueue-only and publish-one controls.
"""

import os
import sys
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, render_template_string, jsonify, request
from loguru import logger

app = Flask(__name__)
_pipeline_thread: threading.Thread = None
_pipeline_running = False
_last_results = []
_next_run_time: datetime = None
_next_publish_time: datetime = None

# Shared uploader instance for auth operations
_uploader = None

def _get_uploader():
    global _uploader
    if _uploader is None:
        from uploader.youtube_uploader import YouTubeUploader
        channel_name = os.getenv("CHANNEL_NAME", "News Channel")
        _uploader = YouTubeUploader(channel_name=channel_name)
    return _uploader

# ── Templates ──────────────────────────────────────────────────────────────────

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>YouTube News Bot — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{--red:#dc1e1e;--red-dk:#b91c1c;--gold:#f5b800;--purple:#7c3aed;--dark:#07090f;--card:#0f1218;--border:#1e2535;--text:#e8eaf0;--muted:#5b6478;--green:#22c55e;--blue:#3b82f6;--orange:#f97316}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--dark);color:var(--text);font-family:'Inter',system-ui,sans-serif;min-height:100vh}
  header{background:var(--card);border-bottom:2px solid var(--red);padding:14px 32px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
  .hd-l{display:flex;align-items:center;gap:14px}
  .logo{width:36px;height:36px;background:var(--red);border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:1.1rem}
  header h1{font-size:1.2rem;font-weight:800;letter-spacing:-.02em}
  header h1 span{color:var(--red)}
  .badge{background:var(--red);color:white;font-size:.62rem;font-weight:700;padding:3px 9px;border-radius:20px;margin-left:8px;letter-spacing:.05em}
  .badge.live{background:#16a34a;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
  .hd-r{font-size:.8rem;color:var(--muted);display:flex;gap:18px;align-items:center}
  #next-run{color:var(--gold);font-weight:600}
  #next-publish{color:var(--orange);font-weight:600}
  main{padding:26px 32px;max-width:1600px;margin:0 auto}
  /* Stats */
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:13px;margin-bottom:24px}
  .stat{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:17px 20px;position:relative;overflow:hidden}
  .stat::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
  .stat.gold::before{background:var(--gold)}.stat.green::before{background:var(--green)}
  .stat.red::before{background:var(--red)}.stat.purple::before{background:var(--purple)}
  .stat.blue::before{background:var(--blue)}.stat.orange::before{background:var(--orange)}
  .stat .n{font-size:2.1rem;font-weight:800;line-height:1}
  .stat .l{color:var(--muted);font-size:.76rem;margin-top:5px;font-weight:500}
  .stat.gold .n{color:var(--gold)}.stat.green .n{color:var(--green)}
  .stat.red .n{color:var(--red)}.stat.purple .n{color:#a78bfa}
  .stat.blue .n{color:var(--blue)}.stat.orange .n{color:var(--orange)}
  /* Panel */
  .panel{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:20px;margin-bottom:18px}
  .ptitle{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:7px}
  .ptitle .dot{width:6px;height:6px;border-radius:50%;background:var(--red)}
  /* Buttons */
  .controls{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
  .btn{display:inline-flex;align-items:center;gap:5px;padding:8px 16px;border-radius:8px;font-weight:600;font-size:.83rem;cursor:pointer;border:none;transition:all .14s}
  .btn-red{background:var(--red);color:white}.btn-red:hover{background:var(--red-dk);transform:translateY(-1px)}
  .btn-gray{background:#1e2535;color:var(--text)}.btn-gray:hover{background:#273045}
  .btn-purple{background:#5b21b6;color:white}.btn-purple:hover{background:#4c1d95}
  .btn-green{background:#15803d;color:white}.btn-green:hover{background:#166534}
  .btn-orange{background:#c2410c;color:white}.btn-orange:hover{background:#9a3412}
  .btn-blue{background:#1d4ed8;color:white}.btn-blue:hover{background:#1e40af}
  .btn-amber{background:#b45309;color:white}.btn-amber:hover{background:#92400e}
  .btn:disabled{opacity:.4;cursor:not-allowed;transform:none!important}
  /* Tabs */
  .tabs{display:flex;border-bottom:1px solid var(--border);margin-bottom:18px;flex-wrap:wrap;gap:0}
  .tab{padding:9px 18px;cursor:pointer;font-size:.83rem;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;transition:all .14s}
  .tab.active{color:white;border-bottom-color:var(--red)}
  .tab-panel{display:none}.tab-panel.active{display:block}
  /* Table */
  .tbl{width:100%;border-collapse:collapse;font-size:.8rem}
  .tbl th{text-align:left;color:var(--muted);padding:7px 11px;border-bottom:1px solid var(--border);font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.05em}
  .tbl td{padding:10px 11px;border-bottom:1px solid #101420;vertical-align:middle}
  .tbl tr:hover td{background:#0c0f18}.tbl tr:last-child td{border-bottom:none}
  .ok{color:#4ade80;font-weight:600}.err{color:#f87171;font-weight:600}
  .dry{color:#facc15;font-weight:600}.pend{color:var(--orange);font-weight:600}
  .proc{color:var(--blue);font-weight:600}
  .stag{background:#171e2e;border-radius:5px;padding:2px 8px;font-size:.7rem;font-weight:600;color:#94a3b8}
  .cattag{background:#1e2535;border-radius:5px;padding:2px 7px;font-size:.68rem;color:#60a5fa}
  .yl{color:#60a5fa;text-decoration:none;font-size:.78rem}.yl:hover{text-decoration:underline}
  .sbadge{background:#5b21b6;color:white;font-size:.62rem;font-weight:700;padding:2px 5px;border-radius:3px;margin-right:4px}
  /* Status bar */
  #sb{padding:9px 16px;border-radius:8px;margin-bottom:16px;display:none;font-size:.83rem;font-weight:500}
  .si{background:#1d3461;border:1px solid #2563eb}.so{background:#14532d;border:1px solid #16a34a}.se{background:#450a0a;border:1px solid #dc2626}
  /* Toggles */
  .togrow{display:flex;align-items:center;gap:11px;padding:8px 0;border-bottom:1px solid var(--border)}.togrow:last-child{border:none}
  .toglbl{flex:1;font-size:.86rem}
  .tog{position:relative;display:inline-block;width:40px;height:22px}
  .tog input{opacity:0;width:0;height:0}
  .sl{position:absolute;cursor:pointer;inset:0;background:#2a3248;border-radius:22px;transition:.24s}
  .sl:before{content:'';position:absolute;height:16px;width:16px;left:3px;bottom:3px;background:white;border-radius:50%;transition:.24s}
  input:checked+.sl{background:var(--red)}input:checked+.sl:before{transform:translateX(18px)}
  .empty{color:var(--muted);text-align:center;padding:28px;font-size:.83rem}

  /* ── Queue pill counts ── */
  .q-pills{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
  .q-pill{padding:4px 12px;border-radius:20px;font-size:.72rem;font-weight:700;letter-spacing:.04em}
  .q-pill.pend{background:#7c2d1240;border:1px solid var(--orange);color:var(--orange)}
  .q-pill.proc{background:#1e3a5f40;border:1px solid var(--blue);color:var(--blue)}
  .q-pill.done{background:#14532d40;border:1px solid var(--green);color:var(--green)}
  .q-pill.fail{background:#450a0a40;border:1px solid var(--red);color:var(--red)}

  /* ── Channel Bar ── */
  .channel-bar{background:var(--card);border:1px solid var(--border);border-radius:13px;padding:16px 20px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;gap:16px}
  .ch-info{display:flex;align-items:center;gap:14px}
  .ch-avatar{width:44px;height:44px;border-radius:50%;border:2px solid var(--red);object-fit:cover;background:#1e2535}
  .ch-avatar-placeholder{width:44px;height:44px;border-radius:50%;border:2px dashed var(--muted);display:flex;align-items:center;justify-content:center;font-size:1.2rem;color:var(--muted);background:#0f1218}
  .ch-details h3{font-size:.95rem;font-weight:700;color:var(--text);margin-bottom:2px}
  .ch-details .ch-sub{font-size:.72rem;color:var(--muted);font-weight:500}
  .ch-details .ch-sub span{color:var(--gold);font-weight:600}
  .ch-not-connected{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:.85rem}
  .ch-not-connected .dot-warn{width:8px;height:8px;border-radius:50%;background:#f59e0b}
  .ch-actions{display:flex;gap:8px;align-items:center}
  .ch-id{font-size:.65rem;color:var(--muted);font-family:monospace}

  /* ── Modal ── */
  .modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(4px);z-index:1000;display:none;align-items:center;justify-content:center}
  .modal-overlay.show{display:flex}
  .modal{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:28px 32px;max-width:440px;width:90%;text-align:center}
  .modal h2{font-size:1.1rem;font-weight:700;margin-bottom:8px}
  .modal p{color:var(--muted);font-size:.85rem;margin-bottom:20px;line-height:1.5}
  .modal .btn-row{display:flex;gap:10px;justify-content:center}
  .modal .spinner{width:28px;height:28px;border:3px solid var(--border);border-top-color:var(--red);border-radius:50%;animation:spin 0.8s linear infinite;margin:14px auto}
  @keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <div class="hd-l">
    <div class="logo">&#128250;</div>
    <h1>YouTube <span>News Bot</span><span class="badge" id="run-badge">IDLE</span></h1>
  </div>
  <div class="hd-r">
    <span>Next scrape: <span id="next-run">—</span></span>
    <span>Next publish: <span id="next-publish">—</span></span>
    <span id="last-run">Last run: —</span>
  </div>
</header>

<main>
  <div id="sb"></div>

  <!-- ── Channel Connection Bar ── -->
  <div class="channel-bar" id="channel-bar">
    <div class="ch-info" id="ch-info">
      <div class="ch-not-connected" id="ch-disconnected">
        <div class="dot-warn"></div>
        <span>No YouTube channel connected</span>
      </div>
      <div id="ch-connected" style="display:none;align-items:center;gap:14px">
        <img class="ch-avatar" id="ch-avatar" src="" alt="Channel">
        <div class="ch-details">
          <h3 id="ch-name">—</h3>
          <div class="ch-sub">
            <span id="ch-subs">0</span> subscribers &bull;
            <span id="ch-vids">0</span> videos &bull;
            <span id="ch-views">0</span> views
          </div>
          <div class="ch-id" id="ch-url"></div>
        </div>
      </div>
    </div>
    <div class="ch-actions">
      <button class="btn btn-green" id="btn-login" onclick="loginYT()">&#128275; Login</button>
      <button class="btn btn-orange" id="btn-logout" onclick="confirmLogout()" style="display:none">&#128682; Logout</button>
      <button class="btn btn-gray" id="btn-switch" onclick="confirmLogout()" style="display:none">&#128260; Switch Channel</button>
    </div>
  </div>

  <div class="stats">
    <div class="stat gold"><div class="n" id="s-total">0</div><div class="l">Total Processed</div></div>
    <div class="stat green"><div class="n" id="s-ok">0</div><div class="l">Shorts Uploaded</div></div>
    <div class="stat purple"><div class="n" id="s-shorts">0</div><div class="l">Bundles Scheduled</div></div>
    <div class="stat red"><div class="n" id="s-fail">0</div><div class="l">Failed</div></div>
    <div class="stat orange"><div class="n" id="s-queue-pending">0</div><div class="l">Queue Pending</div></div>
    <div class="stat blue"><div class="n" id="s-sources">5</div><div class="l">News Sources</div></div>
  </div>

  <div class="panel">
    <div class="ptitle"><span class="dot"></span>Pipeline Controls</div>
    <div class="controls">
      <button class="btn btn-red"    id="btn-run"     onclick="triggerRun(false)">&#9654; Run Now</button>
      <button class="btn btn-gray"   id="btn-dry"     onclick="triggerRun(true)">&#128221; Dry Run</button>
      <button class="btn btn-blue"   id="btn-enqueue" onclick="triggerEnqueue()">&#128229; Scrape &amp; Enqueue</button>
      <button class="btn btn-amber"  id="btn-publish-sel" onclick="triggerPublishSelected()" disabled>&#9193; Publish Selected (<span id="sel-count">0</span>)</button>
      <button class="btn btn-gray"               onclick="refreshAll()">&#8635; Refresh</button>
      <button class="btn btn-purple"             onclick="window.open('https://studio.youtube.com','_blank')">&#128250; YouTube Studio</button>
    </div>
    <div style="margin-top:10px;border-top:1px solid var(--border);padding-top:10px">
      <div class="ptitle" style="margin-bottom:8px"><span class="dot" style="background:var(--orange)"></span>Bundle Shorts</div>
      <div class="controls">
        <button class="btn btn-orange" id="btn-bundle-daily"  onclick="triggerBundle('daily',false)">&#128240; Daily Top-30 Bundle</button>
        <button class="btn btn-gray"   id="btn-bundle-daily-dry" onclick="triggerBundle('daily',true)">&#128221; Daily Dry Run</button>
        <button class="btn btn-purple" id="btn-bundle-weekly" onclick="triggerBundle('weekly',false)">&#128197; Weekly Top-100 Bundle</button>
        <button class="btn btn-gray"   id="btn-bundle-weekly-dry" onclick="triggerBundle('weekly',true)">&#128221; Weekly Dry Run</button>
      </div>
      <p style="color:var(--muted);font-size:.73rem;margin-top:7px">Daily bundles auto-schedule to 8:00 AM &amp; 6:00 PM IST &bull; Weekly bundles schedule for Sunday 10:00 AM IST</p>
    </div>
  </div>

  <div class="panel">
    <div class="tabs">
      <div class="tab active" onclick="switchTab('queue',this)">&#128240; Queue</div>
      <div class="tab"        onclick="switchTab('shorts',this)">&#9889; Shorts</div>
      <div class="tab"        onclick="switchTab('sources',this)">&#9881; Sources</div>
    </div>

    <!-- Queue Tab -->
    <div class="tab-panel active" id="panel-queue">
      <div class="q-pills">
        <span class="q-pill pend" id="qp-pending">0 pending</span>
        <span class="q-pill proc" id="qp-processing">0 processing</span>
        <span class="q-pill done" id="qp-done">0 done</span>
        <span class="q-pill fail" id="qp-fail">0 failed</span>
      </div>
      <table class="tbl">
        <thead><tr>
          <th style="width:32px"><input type="checkbox" id="chk-all" onclick="toggleSelectAll(this)" title="Select all pending"></th>
          <th>Queued</th><th>Source</th><th>Category</th><th>Title</th><th>Status</th>
        </tr></thead>
        <tbody id="queue-body"><tr><td colspan="6" class="empty">Queue is empty.</td></tr></tbody>
      </table>
    </div>

    <!-- Shorts Tab (individual + bundle) -->
    <div class="tab-panel" id="panel-shorts">
      <table class="tbl">
        <thead><tr><th>Time</th><th>Source</th><th>Title</th><th>Type</th><th>Status</th><th>Shorts Link</th></tr></thead>
        <tbody id="shorts-body"><tr><td colspan="6" class="empty">No shorts yet.</td></tr></tbody>
      </table>
    </div>

    <!-- Sources Tab -->
    <div class="tab-panel" id="panel-sources">
      <div class="togrow"><span class="toglbl">The Hindu</span><label class="tog"><input type="checkbox" checked><span class="sl"></span></label></div>
      <div class="togrow"><span class="toglbl">India Today</span><label class="tog"><input type="checkbox" checked><span class="sl"></span></label></div>
      <div class="togrow"><span class="toglbl">NDTV</span><label class="tog"><input type="checkbox" checked><span class="sl"></span></label></div>
      <div class="togrow"><span class="toglbl">Times of India</span><label class="tog"><input type="checkbox" checked><span class="sl"></span></label></div>
      <div class="togrow"><span class="toglbl">Hindustan Times</span><label class="tog"><input type="checkbox" checked><span class="sl"></span></label></div>
    </div>
  </div>
</main>

<!-- ── Logout Confirmation Modal ── -->
<div class="modal-overlay" id="modal-logout">
  <div class="modal">
    <h2>&#128682; Logout from YouTube?</h2>
    <p>This will disconnect the current channel. You'll need to login again (or with a different Google account) before videos can be uploaded.</p>
    <div class="btn-row">
      <button class="btn btn-gray" onclick="closeModal()">Cancel</button>
      <button class="btn btn-orange" onclick="doLogout()">Logout</button>
    </div>
  </div>
</div>

<!-- ── Login Waiting Modal ── -->
<div class="modal-overlay" id="modal-login">
  <div class="modal">
    <h2>&#128275; Waiting for Google Login...</h2>
    <div class="spinner"></div>
    <p>A browser window should open for Google OAuth. Complete the login there.<br>This page will update automatically.</p>
    <div class="btn-row">
      <button class="btn btn-gray" onclick="closeModal()">Cancel</button>
    </div>
  </div>
</div>

<script>
/* ── Tab switching ── */
function switchTab(n,el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('panel-'+n).classList.add('active');
}

/* ── Status bar ── */
function showSb(msg,type){
  const el=document.getElementById('sb');
  el.textContent=msg; el.className='s'+type; el.style.display='block';
  setTimeout(()=>el.style.display='none',6000);
}

/* ── Modals ── */
function closeModal(){
  document.querySelectorAll('.modal-overlay').forEach(m=>m.classList.remove('show'));
}

/* ── YouTube Auth ── */
function formatCount(n){
  n=parseInt(n)||0;
  if(n>=1000000) return (n/1000000).toFixed(1)+'M';
  if(n>=1000) return (n/1000).toFixed(1)+'K';
  return n.toString();
}

async function refreshAuth(){
  try{
    const d=await(await fetch('/api/auth/status')).json();
    const disc=document.getElementById('ch-disconnected');
    const conn=document.getElementById('ch-connected');
    const btnLogin=document.getElementById('btn-login');
    const btnLogout=document.getElementById('btn-logout');
    const btnSwitch=document.getElementById('btn-switch');

    if(d.authenticated && d.channel){
      disc.style.display='none';
      conn.style.display='flex';
      document.getElementById('ch-name').textContent=d.channel.title||'Unknown';
      document.getElementById('ch-subs').textContent=formatCount(d.channel.subscriber_count);
      document.getElementById('ch-vids').textContent=formatCount(d.channel.video_count);
      document.getElementById('ch-views').textContent=formatCount(d.channel.view_count);
      document.getElementById('ch-url').textContent=d.channel.custom_url||d.channel.channel_id||'';
      const av=document.getElementById('ch-avatar');
      if(d.channel.thumbnail){av.src=d.channel.thumbnail;av.style.display='block';}
      else av.style.display='none';
      btnLogin.style.display='none';
      btnLogout.style.display='inline-flex';
      btnSwitch.style.display='inline-flex';
    }else{
      disc.style.display='flex';
      conn.style.display='none';
      btnLogin.style.display='inline-flex';
      btnLogout.style.display='none';
      btnSwitch.style.display='none';
    }
  }catch(e){console.error('Auth check failed:',e);}
}

async function loginYT(){
  document.getElementById('modal-login').classList.add('show');
  try{
    const r=await fetch('/api/auth/login',{method:'POST'});
    const d=await r.json();
    closeModal();
    if(d.success){
      showSb('Successfully connected to YouTube channel: '+d.channel_name,'o');
      refreshAuth();
    }else{
      showSb('Login failed: '+(d.error||'Unknown error'),'e');
    }
  }catch(e){
    closeModal();
    showSb('Login error: '+e,'e');
  }
}

function confirmLogout(){
  document.getElementById('modal-logout').classList.add('show');
}

async function doLogout(){
  closeModal();
  try{
    const r=await fetch('/api/auth/logout',{method:'POST'});
    const d=await r.json();
    if(d.success){
      showSb('Logged out from YouTube. Login again to connect a channel.','o');
      refreshAuth();
    }else{
      showSb('Logout failed: '+(d.error||'Unknown error'),'e');
    }
  }catch(e){showSb('Logout error: '+e,'e');}
}

/* ── Pipeline ── */
async function triggerRun(dry){
  const btn=document.getElementById(dry?'btn-dry':'btn-run');
  btn.disabled=true;
  document.getElementById('run-badge').textContent='RUNNING';
  document.getElementById('run-badge').className='badge live';
  showSb('Pipeline started...','i');
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dry_run:dry})});
    const d=await r.json();
    showSb(d.message,d.status==='started'?'i':'e');
  }catch(e){
    showSb('Error: '+e,'e');
    btn.disabled=false;
    document.getElementById('run-badge').textContent='IDLE';
    document.getElementById('run-badge').className='badge';
  }
  setTimeout(pollStatus,3000);
}

async function triggerEnqueue(){
  const btn=document.getElementById('btn-enqueue');
  btn.disabled=true;
  showSb('Scraping and enqueueing articles...','i');
  try{
    const r=await fetch('/api/queue/enqueue',{method:'POST'});
    const d=await r.json();
    showSb(d.message, d.added>0?'o':'i');
    refreshQueue();
  }catch(e){
    showSb('Enqueue error: '+e,'e');
  }finally{
    btn.disabled=false;
  }
}

async function triggerPublishOne(){
  const btn=document.getElementById('btn-publish');
  btn.disabled=true;
  document.getElementById('run-badge').textContent='RUNNING';
  document.getElementById('run-badge').className='badge live';
  showSb('Publishing next article from queue...','i');
  try{
    const r=await fetch('/api/queue/publish-one',{method:'POST'});
    const d=await r.json();
    showSb(d.message, d.status==='started'?'i':'e');
  }catch(e){
    showSb('Publish error: '+e,'e');
    document.getElementById('run-badge').textContent='IDLE';
    document.getElementById('run-badge').className='badge';
  }finally{
    btn.disabled=false;
  }
  setTimeout(pollStatus,3000);
}

async function triggerPublishN(n){
  const btn=document.getElementById('btn-publish-'+n);
  btn.disabled=true;
  document.getElementById('run-badge').textContent='RUNNING';
  document.getElementById('run-badge').className='badge live';
  showSb('Publishing '+n+' articles from queue...','i');
  try{
    const r=await fetch('/api/queue/publish-n',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:n})});
    const d=await r.json();
    showSb(d.message, d.status==='started'?'i':'e');
    if(d.status!=='started'){
      document.getElementById('run-badge').textContent='IDLE';
      document.getElementById('run-badge').className='badge';
    }
  }catch(e){
    showSb('Publish error: '+e,'e');
    document.getElementById('run-badge').textContent='IDLE';
    document.getElementById('run-badge').className='badge';
  }finally{
    btn.disabled=false;
  }
  setTimeout(pollStatus,3000);
}

async function pollStatus(){
  try{
    const d=await(await fetch('/api/status')).json();
    if(d.next_run)    document.getElementById('next-run').textContent=d.next_run;
    if(d.next_publish)document.getElementById('next-publish').textContent=d.next_publish;
    if(!d.running){
      document.getElementById('run-badge').textContent='IDLE';
      document.getElementById('run-badge').className='badge';
      document.querySelectorAll('.btn').forEach(b=>b.disabled=false);
      showSb('Done!','o');
      refreshAll();
    } else { setTimeout(pollStatus,4000); }
  }catch(e){}
}

/* ── Queue selection ── */
let _selectedIds = new Set();

function updateSelectionUI(){
  const n = _selectedIds.size;
  document.getElementById('sel-count').textContent = n;
  document.getElementById('btn-publish-sel').disabled = n === 0;
  // Sync header checkbox
  const allChks = document.querySelectorAll('.q-chk');
  const chkAll  = document.getElementById('chk-all');
  if(chkAll){
    const checked = [...allChks].filter(c=>c.checked).length;
    chkAll.checked       = allChks.length > 0 && checked === allChks.length;
    chkAll.indeterminate = checked > 0 && checked < allChks.length;
  }
}

function toggleSelectAll(chkEl){
  document.querySelectorAll('.q-chk').forEach(c=>{
    c.checked = chkEl.checked;
    if(chkEl.checked) _selectedIds.add(c.dataset.id);
    else              _selectedIds.delete(c.dataset.id);
  });
  updateSelectionUI();
}

function onRowCheck(chkEl){
  if(chkEl.checked) _selectedIds.add(chkEl.dataset.id);
  else              _selectedIds.delete(chkEl.dataset.id);
  updateSelectionUI();
}

/* ── Queue ── */
function qStatusCell(s){
  if(s==='pending')    return '<span class="pend">&#9201; Pending</span>';
  if(s==='processing') return '<span class="proc">&#9654; Processing</span>';
  if(s==='done')       return '<span class="ok">&#10004; Done</span>';
  if(s==='failed')     return '<span class="err">&#10007; Failed</span>';
  return s;
}

async function refreshQueue(){
  try{
    const d=await(await fetch('/api/queue')).json();
    // Update pills
    document.getElementById('qp-pending').textContent   = d.stats.pending+' pending';
    document.getElementById('qp-processing').textContent= d.stats.processing+' processing';
    document.getElementById('qp-done').textContent      = d.stats.done+' done';
    document.getElementById('qp-fail').textContent      = d.stats.failed+' failed';
    // Update orange stat card
    document.getElementById('s-queue-pending').textContent = d.stats.pending;

    const tbody=document.getElementById('queue-body');
    if(!d.items.length){
      tbody.innerHTML='<tr><td colspan="6" class="empty">Queue is empty.</td></tr>';
      _selectedIds.clear();
      updateSelectionUI();
      return;
    }

    // Preserve current selection only for IDs still in the list
    const currentIds = new Set(d.items.map(q=>q.id));
    _selectedIds = new Set([..._selectedIds].filter(id=>currentIds.has(id)));

    tbody.innerHTML=d.items.map(q=>{
      const isPending = q.status === 'pending';
      const checked   = isPending && _selectedIds.has(q.id) ? 'checked' : '';
      const chkCell   = isPending
        ? `<td><input type="checkbox" class="q-chk" data-id="${q.id}" ${checked} onchange="onRowCheck(this)"></td>`
        : `<td></td>`;
      return `
      <tr class="${isPending ? 'q-row-pending' : ''}">
        ${chkCell}
        <td style="color:var(--muted);white-space:nowrap;font-size:.75rem">${(q.queued_at||'').slice(0,16).replace('T',' ')}</td>
        <td><span class="stag">${q.source||'?'}</span></td>
        <td><span class="cattag">${q.category||''}</span></td>
        <td style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${q.title||''}">${(q.title||'').slice(0,65)}${(q.title?.length||0)>65?'…':''}</td>
        <td>${qStatusCell(q.status)}</td>
      </tr>`;
    }).join('');
    updateSelectionUI();
  }catch(e){console.error(e);}
}

async function triggerPublishSelected(){
  if(_selectedIds.size === 0) return;
  const ids = [..._selectedIds];
  const btn = document.getElementById('btn-publish-sel');
  btn.disabled = true;
  document.getElementById('run-badge').textContent='RUNNING';
  document.getElementById('run-badge').className='badge live';
  showSb('Publishing '+ids.length+' selected article'+(ids.length>1?'s':'')+'...','i');
  try{
    const r=await fetch('/api/queue/publish-selected',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ids})
    });
    const d=await r.json();
    showSb(d.message, d.status==='started'?'i':'e');
    if(d.status==='started'){
      _selectedIds.clear();
      updateSelectionUI();
    } else {
      document.getElementById('run-badge').textContent='IDLE';
      document.getElementById('run-badge').className='badge';
    }
  }catch(e){
    showSb('Publish error: '+e,'e');
    document.getElementById('run-badge').textContent='IDLE';
    document.getElementById('run-badge').className='badge';
  }finally{
    btn.disabled = _selectedIds.size === 0;
  }
  setTimeout(pollStatus,3000);
}

/* ── Pipeline logs ── */
function stCell(r){
  if(r.status==='success') return '<span class="ok">&#10004; Success</span>';
  if(r.status==='skipped') return '<span class="dry">&#8960; Dry Run</span>';
  return '<span class="err">&#10007; '+(r.error||'Failed').slice(0,30)+'</span>';
}
async function refreshLogs(){
  try{
    const d=await(await fetch('/api/logs')).json();
    document.getElementById('s-total').textContent=d.total;
    document.getElementById('s-ok').textContent=d.success;
    document.getElementById('s-fail').textContent=d.failed;
    document.getElementById('s-shorts').textContent=d.bundles||0;
    if(d.last_run) document.getElementById('last-run').textContent='Last: '+d.last_run;
    if(d.next_run) document.getElementById('next-run').textContent=d.next_run;
    const sb=document.getElementById('shorts-body');
    if(!d.results.length){
      sb.innerHTML='<tr><td colspan="6" class="empty">No shorts yet.</td></tr>';
      return;
    }
    sb.innerHTML=d.results.map(r=>{
      const url=r.shorts_url&&r.shorts_url!=='DRY_RUN'
        ?`<a class="yl" href="${r.shorts_url}" target="_blank"><span class="sbadge">#S</span>Watch Short</a>`
        :(r.shorts_url==='DRY_RUN'?'<span class="dry">DRY RUN</span>':'—');
      const typeTag=r.type==='bundle'?'<span class="cattag">Bundle</span>':'<span class="stag">Individual</span>';
      return `<tr><td style="color:var(--muted);white-space:nowrap">${(r.timestamp||'').slice(0,19).replace('T',' ')}</td><td><span class="stag">${r.source||'?'}</span></td><td>${(r.title||'').slice(0,56)}${(r.title?.length||0)>56?'…':''}</td><td>${typeTag}</td><td><span class="ok">&#10004; Success</span></td><td>${url}</td></tr>`;
    }).join('');
  }catch(e){console.error(e);}
}

function refreshAll(){
  refreshAuth();
  refreshQueue();
  refreshLogs();
}

/* ── Bundle trigger ── */
async function triggerBundle(mode, dry){
  const btnId = dry ? `btn-bundle-${mode}-dry` : `btn-bundle-${mode}`;
  const btn = document.getElementById(btnId);
  if(btn) btn.disabled = true;
  document.getElementById('run-badge').textContent='RUNNING';
  document.getElementById('run-badge').className='badge live';
  const label = mode==='daily' ? 'Daily Top-30' : 'Weekly Top-100';
  showSb((dry?'[Dry Run] ':'')+'Generating '+label+' bundle…','i');
  try{
    const r = await fetch('/api/bundle/'+mode, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({dry_run: dry})
    });
    const d = await r.json();
    showSb(d.message, d.status==='started'?'i':'e');
    if(d.status !== 'started'){
      document.getElementById('run-badge').textContent='IDLE';
      document.getElementById('run-badge').className='badge';
      if(btn) btn.disabled = false;
    }
  }catch(e){
    showSb('Bundle error: '+e,'e');
    document.getElementById('run-badge').textContent='IDLE';
    document.getElementById('run-badge').className='badge';
    if(btn) btn.disabled = false;
  }
  setTimeout(pollStatus, 3000);
}

/* ── Init ── */
refreshAll();
setInterval(refreshQueue, 10000);
setInterval(refreshLogs,  15000);
setInterval(pollStatus,   10000);
</script>
</body>
</html>
"""

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML)


# ── Auth Routes ────────────────────────────────────────────────────────────────

@app.route("/api/auth/status")
def api_auth_status():
    """Check if YouTube is authenticated and return channel info."""
    up = _get_uploader()
    authenticated = up.is_authenticated()
    channel = {}
    if authenticated:
        try:
            channel = up.get_channel_info()
        except Exception as e:
            logger.warning(f"Could not get channel info: {e}")
    return jsonify({
        "authenticated": authenticated,
        "channel": channel,
    })


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Trigger YouTube OAuth login flow (opens browser)."""
    global _uploader
    up = _get_uploader()
    try:
        up._service = None
        success = up.authenticate()
        if success:
            channel = up.get_channel_info()
            return jsonify({
                "success": True,
                "channel_name": channel.get("title", "Unknown"),
                "channel": channel,
            })
        else:
            return jsonify({"success": False, "error": "Authentication failed"})
    except Exception as e:
        logger.error(f"Login failed: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Logout from YouTube (remove saved token)."""
    global _uploader
    up = _get_uploader()
    try:
        success = up.logout()
        _uploader = None
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Logout failed: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Queue Routes ───────────────────────────────────────────────────────────────

@app.route("/api/queue")
def api_queue():
    """Return queue stats + preview of recent items."""
    import article_queue as q
    stats = q.queue_stats()
    items = q.get_queue_preview(limit=50)
    return jsonify({"stats": stats, "items": items})


@app.route("/api/queue/enqueue", methods=["POST"])
def api_queue_enqueue():
    """Scrape all sources and add new articles to the queue (does not process)."""
    global _pipeline_running
    if _pipeline_running:
        return jsonify({"status": "busy", "message": "Pipeline is currently running.", "added": 0})

    try:
        from pipeline import NewsPipeline
        p = NewsPipeline()
        added = p.scrape_and_enqueue()
        return jsonify({
            "status": "ok",
            "added": added,
            "message": f"Enqueued {added} new articles." if added else "No new articles found.",
        })
    except Exception as e:
        logger.error(f"Enqueue failed: {e}")
        return jsonify({"status": "error", "added": 0, "message": str(e)})


@app.route("/api/queue/publish-one", methods=["POST"])
def api_queue_publish_one():
    """Pop the next pending article from the queue and process it."""
    global _pipeline_thread, _pipeline_running
    if _pipeline_running:
        return jsonify({"status": "already_running", "message": "Pipeline already running."})

    import article_queue as q
    if q.queue_depth() == 0:
        return jsonify({"status": "empty", "message": "Queue is empty — nothing to publish."})

    data = request.get_json(force=True, silent=True) or {}
    dry_run = data.get("dry_run", False)

    def run():
        global _pipeline_running, _last_results
        _pipeline_running = True
        try:
            from pipeline import NewsPipeline
            p = NewsPipeline()
            result = p.process_one(dry_run=dry_run)
            if result:
                _last_results = [result]
        except Exception as e:
            logger.error(f"Dashboard publish-one failed: {e}")
        finally:
            _pipeline_running = False

    _pipeline_thread = threading.Thread(target=run, daemon=True)
    _pipeline_thread.start()
    return jsonify({"status": "started", "message": "Publishing next queued article..."})


@app.route("/api/queue/publish-selected", methods=["POST"])
def api_queue_publish_selected():
    """Publish a specific set of articles by their IDs (selected in the dashboard)."""
    global _pipeline_thread, _pipeline_running
    if _pipeline_running:
        return jsonify({"status": "already_running", "message": "Pipeline already running."})

    data = request.get_json(force=True, silent=True) or {}
    ids: list = data.get("ids", [])
    dry_run: bool = data.get("dry_run", False)

    if not ids:
        return jsonify({"status": "empty", "message": "No articles selected."})

    # Clamp to a safe max to avoid accidental mass-publish
    ids = ids[:20]

    def run():
        global _pipeline_running, _last_results
        _pipeline_running = True
        results = []
        try:
            from pipeline import NewsPipeline
            import article_queue as aq
            p = NewsPipeline()
            for article_id in ids:
                stub = aq.pop_by_id(article_id)
                if stub is None:
                    logger.warning(f"publish-selected: {article_id} not found / not pending — skipping.")
                    continue
                article = aq.stub_to_article(stub)
                result = p._process_article(article, dry_run=dry_run)
                p._save_result(result)
                if result.status == "success":
                    aq.mark_done(article.id)
                else:
                    aq.mark_failed(article.id, result.error)
                results.append(result)
            _last_results = results
        except Exception as e:
            logger.error(f"Dashboard publish-selected failed: {e}")
        finally:
            _pipeline_running = False

    _pipeline_thread = threading.Thread(target=run, daemon=True)
    _pipeline_thread.start()
    n = len(ids)
    return jsonify({"status": "started", "message": f"Publishing {n} selected article{'s' if n != 1 else ''}..."})


# ── Pipeline Routes ────────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def api_run():
    global _pipeline_thread, _pipeline_running
    if _pipeline_running:
        return jsonify({"status": "already_running", "message": "Pipeline already running."})

    data = request.get_json(force=True, silent=True) or {}
    dry_run = data.get("dry_run", False)

    def run():
        global _pipeline_running, _last_results
        _pipeline_running = True
        try:
            from pipeline import NewsPipeline
            p = NewsPipeline()
            _last_results = p.run(dry_run=dry_run)
        except Exception as e:
            logger.error(f"Dashboard pipeline run failed: {e}")
        finally:
            _pipeline_running = False

    _pipeline_thread = threading.Thread(target=run, daemon=True)
    _pipeline_thread.start()
    return jsonify({"status": "started", "message": "Pipeline started in background."})


@app.route("/api/status")
def api_status():
    next_run_str = ""
    if _next_run_time:
        delta = _next_run_time - datetime.now()
        total_s = max(0, int(delta.total_seconds()))
        h, rem = divmod(total_s, 3600)
        m, s   = divmod(rem, 60)
        next_run_str = f"{h:02d}h {m:02d}m {s:02d}s"

    next_pub_str = ""
    if _next_publish_time:
        delta = _next_publish_time - datetime.now()
        total_s = max(0, int(delta.total_seconds()))
        h, rem = divmod(total_s, 3600)
        m, s   = divmod(rem, 60)
        next_pub_str = f"{h:02d}h {m:02d}m {s:02d}s"

    return jsonify({
        "running": _pipeline_running,
        "next_run": next_run_str,
        "next_publish": next_pub_str,
    })


# ── Bundle Routes ────────────────────────────────────────────────────────

@app.route("/api/bundle/daily", methods=["POST"])
def api_bundle_daily():
    """Generate + schedule the daily Top-30 bundle Shorts (3 parts)."""
    global _pipeline_thread, _pipeline_running
    if _pipeline_running:
        return jsonify({"status": "already_running", "message": "Pipeline already running."})

    data = request.get_json(force=True, silent=True) or {}
    dry_run = data.get("dry_run", False)

    def run():
        global _pipeline_running, _last_results
        _pipeline_running = True
        try:
            from pipeline import NewsPipeline
            p = NewsPipeline()
            urls = p.run_daily_bundle(dry_run=dry_run)
            logger.info(f"Dashboard daily bundle: {len(urls)} parts processed.")
        except Exception as e:
            logger.error(f"Dashboard daily bundle failed: {e}")
        finally:
            _pipeline_running = False

    _pipeline_thread = threading.Thread(target=run, daemon=True)
    _pipeline_thread.start()
    mode_str = "[Dry Run] " if dry_run else ""
    return jsonify({"status": "started", "message": f"{mode_str}Daily Top-30 bundle started in background."})


@app.route("/api/bundle/weekly", methods=["POST"])
def api_bundle_weekly():
    """Generate + schedule the weekly Top-100 bundle Shorts (10 parts)."""
    global _pipeline_thread, _pipeline_running
    if _pipeline_running:
        return jsonify({"status": "already_running", "message": "Pipeline already running."})

    data = request.get_json(force=True, silent=True) or {}
    dry_run = data.get("dry_run", False)

    def run():
        global _pipeline_running, _last_results
        _pipeline_running = True
        try:
            from pipeline import NewsPipeline
            p = NewsPipeline()
            urls = p.run_weekly_bundle(dry_run=dry_run)
            logger.info(f"Dashboard weekly bundle: {len(urls)} parts processed.")
        except Exception as e:
            logger.error(f"Dashboard weekly bundle failed: {e}")
        finally:
            _pipeline_running = False

    _pipeline_thread = threading.Thread(target=run, daemon=True)
    _pipeline_thread.start()
    mode_str = "[Dry Run] " if dry_run else ""
    return jsonify({"status": "started", "message": f"{mode_str}Weekly Top-100 bundle started in background."})


@app.route("/api/logs")
def api_logs():
    log_path = Path("./logs/pipeline_log.jsonl")
    results = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        for line in reversed(lines[-50:]):
            try:
                results.append(json.loads(line))
            except Exception:
                pass

    total   = len(results)
    success = sum(1 for r in results if r.get("status") == "success")
    failed  = sum(1 for r in results if r.get("status") == "failed")
    shorts  = sum(1 for r in results if r.get("shorts_url") and r.get("shorts_url") not in ("", "DRY_RUN") and r.get("type") != "bundle")
    bundles = sum(1 for r in results if r.get("type") == "bundle" and r.get("shorts_url") and r.get("shorts_url") not in ("", "DRY_RUN"))
    last_run = results[0].get("timestamp", "")[:19].replace("T", " ") if results else ""

    next_run_str = ""
    if _next_run_time:
        delta = _next_run_time - datetime.now()
        total_s = max(0, int(delta.total_seconds()))
        h, rem = divmod(total_s, 3600)
        m, s   = divmod(rem, 60)
        next_run_str = f"{h:02d}h {m:02d}m"

    return jsonify({
        "total": total, "success": success, "failed": failed,
        "shorts": shorts, "bundles": bundles,
        "last_run": last_run, "next_run": next_run_str, "results": results[:50],
    })



if __name__ == "__main__":
    import schedule
    import time

    publish_interval = int(os.getenv("PUBLISH_INTERVAL_MINUTES", "15"))
    scrape_interval  = int(os.getenv("SCRAPE_INTERVAL_MINUTES",  "120"))

    def run_scrape_job():
        global _pipeline_running, _next_run_time
        if _pipeline_running:
            return
        _pipeline_running = True
        try:
            logger.info("Scheduled scrape job starting...")
            from pipeline import NewsPipeline
            p = NewsPipeline()
            p.scrape_and_enqueue()
        except Exception as e:
            logger.error(f"Scheduled scrape failed: {e}")
        finally:
            _pipeline_running = False
            _next_run_time = datetime.now() + timedelta(minutes=scrape_interval)

    def run_publish_job():
        global _pipeline_running, _next_publish_time, _last_results
        if _pipeline_running:
            return
        _pipeline_running = True
        try:
            import article_queue as q
            if q.queue_depth() == 0:
                logger.info("Drip tick: queue empty, skipping.")
                return
            logger.info("Drip tick: publishing next queued article...")
            from pipeline import NewsPipeline
            p = NewsPipeline()
            result = p.process_one(dry_run=False)
            if result:
                _last_results = [result]
        except Exception as e:
            logger.error(f"Drip publish failed: {e}")
        finally:
            _pipeline_running = False
            _next_publish_time = datetime.now() + timedelta(minutes=publish_interval)

    def job_loop():
        global _next_run_time, _next_publish_time
        logger.info(
            f"Scheduler started — scrape every {scrape_interval} min, "
            f"publish every {publish_interval} min."
        )
        schedule.every(scrape_interval).minutes.do(
            lambda: threading.Thread(target=run_scrape_job, daemon=True).start()
        )
        schedule.every(publish_interval).minutes.do(
            lambda: threading.Thread(target=run_publish_job, daemon=True).start()
        )
        _next_run_time     = datetime.now() + timedelta(minutes=scrape_interval)
        _next_publish_time = datetime.now() + timedelta(minutes=publish_interval)
        while True:
            schedule.run_pending()
            time.sleep(15)

    threading.Thread(target=job_loop, daemon=True).start()

    port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", 5050)))
    logger.info(f"Dashboard running at http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
