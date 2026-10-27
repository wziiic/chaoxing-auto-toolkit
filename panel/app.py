# -*- coding: utf-8 -*-
"""
学习通作业自动化 · 本地控制面板 + HTTP API

启动:
    python lxt-auto/panel/app.py
    # 或指定端口
    set LXT_PANEL_PORT=9000 && python lxt-auto/panel/app.py

打开:
    http://127.0.0.1:8765/

API 文档:
    http://127.0.0.1:8765/docs

核心接口:
    GET  /api/works                    作业清单
    GET  /api/questions/{workId}       题目
    POST /api/jobs                     提交异步任务
    GET  /api/jobs/{jobId}             任务进度
    GET  /api/config | POST /api/config   LLM 配置
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # lxt-auto/
sys.path.insert(0, ROOT)

import lxt  # noqa: E402  复用 Cookie 解密 / 表单提取 / 题目解析

PY = sys.executable
CONFIG_PATH = os.path.join(HERE, "config.json")
WORKS_DIR = os.path.join(ROOT, "works")
STATIC = os.path.join(HERE, "static")

DEFAULT_CONFIG = {
    "port": 8765,
    # 监听地址：127.0.0.1 = 只有本机能访问（默认，安全）
    #           0.0.0.0  = 局域网可访问（务必同时设置 access_token）
    "host": "127.0.0.1",
    # 访问口令。非空时，/api/* 必须带 X-Access-Token 或 ?token=
    "access_token": "",
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "",
        "temperature": 0,
    },
    "auto_submit": False,          # 默认只暂存，不交卷
    "default_skip_judge": False,
}

app = FastAPI(title="学习通作业面板", version="1.0")


@app.middleware("http")
async def auth_guard(request, call_next):
    """设置了 access_token 时，/api/* 必须带口令。"""
    cfg = load_config()
    token = (cfg.get("access_token") or "").strip()
    path = request.url.path

    if token and path.startswith("/api"):
        given = (request.headers.get("X-Access-Token")
                 or request.query_params.get("token")
                 or "")
        if given != token:
            return JSONResponse({"detail": "缺少或错误的访问口令"}, status_code=401)

    return await call_next(request)


# ---------------------------------------------------------------- 配置

def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 工具

def run_script(args: List[str], timeout: int = 900) -> Dict[str, Any]:
    """跑 lxt-auto 下的脚本，返回 {code, out}。"""
    r = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=timeout)
    return {"code": r.returncode, "out": (r.stdout or "") + (r.stderr or "")}


def work_dir(work_id: str) -> str:
    return os.path.join(WORKS_DIR, work_id)


def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_worklist() -> List[dict]:
    path = os.path.join(ROOT, "worklist.json")
    return load_json(path, []) or []


def refresh_worklist() -> Dict[str, Any]:
    return run_script([os.path.join(ROOT, "list_works.py")], timeout=180)


# ---------------------------------------------------------------- LLM 作答

def available_keys(question: dict) -> List[str]:
    return [o["data"] for o in question["options"] if o.get("data")]


def validate_answer(question: dict, ans: str) -> Optional[str]:
    """校验并规范化 LLM 给出的答案；不合法返回 None。"""
    if ans is None:
        return None
    ans = str(ans).strip()
    if not ans:
        return None

    qtype = question["type"]
    keys = available_keys(question)

    if qtype == "判断题":
        low = ans.lower()
        if low in ("true", "对", "t", "√", "正确", "yes", "a"):
            return "true"
        if low in ("false", "错", "f", "×", "错误", "no", "b"):
            return "false"
        return None

    if qtype == "多选题":
        # 支持两种形态：{"A":1,"B":0} 逐项表决，或 "ABD" 字符串
        if isinstance(ans, dict):
            picked = {str(k).upper() for k, v in ans.items() if str(v) not in ("0", "false", "False", "")}
            letters = sorted(c for c in picked if c in keys)
            return "".join(letters) or None
        letters = sorted({c for c in ans.upper() if c in keys})
        return "".join(letters) or None

    # 单选
    for c in ans.upper():
        if c in keys:
            return c
    return None


def build_prompt(questions: List[dict]) -> str:
    lines = []
    for q in questions:
        opts = " / ".join(f"{o['data']}={o['content']}" for o in q["options"])
        lines.append(f"#{q['qid']} [{q['type']}] {q['stem']}\n   选项: {opts}")
    body = "\n".join(lines)
    return (
        "请为下列题目作答。规则：\n"
        "1. 只输出一个 JSON 对象，键是题号（#后面的数字），值是答案。\n"
        "2. 单选题：一个字母，如 \"B\"。\n"
        "3. 多选题：多个字母按键名升序连写，如 \"ABD\"。\n"
        "   逐项判断，符合题意才选；只选确实符合的选项，不要凑成全选。\n"
        "4. 判断题：\"true\" 或 \"false\"。\n"
        "5. 只能使用题目给出的选项字母/取值，不要自创。\n"
        "6. 不要输出解释、不要用 markdown 代码块。\n\n"
        f"题目：\n{body}"
    )


def call_llm(prompt: str, cfg: dict) -> Dict[str, str]:
    llm = cfg["llm"]
    if not llm.get("api_key"):
        raise RuntimeError("还没配置 LLM API Key（面板「设置」里填）")

    url = llm["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": llm.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": "你是严谨的答题助手，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": llm.get("temperature", 0),
        "stream": False,
    }
    r = requests.post(url, json=payload, timeout=180, headers={
        "Authorization": f"Bearer {llm['api_key']}",
        "Content-Type": "application/json",
    })
    if r.status_code != 200:
        raise RuntimeError(f"LLM 返回 {r.status_code}: {r.text[:300]}")

    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        raise RuntimeError(f"LLM 输出里找不到 JSON: {content[:200]}")
    return json.loads(m.group(0))


def answer_work(work_id: str, cfg: dict, save: bool = True,
                submit: bool = False, log=None) -> Dict[str, Any]:
    log = log or (lambda s: None)
    qfile = os.path.join(work_dir(work_id), "questions.json")
    questions = load_json(qfile)
    if not questions:
        raise RuntimeError(f"{work_id} 还没解析题目，请先点「抓题」")

    log(f"共 {len(questions)} 题，准备请求 LLM…")
    raw = call_llm(build_prompt(questions), cfg)
    log(f"LLM 返回 {len(raw)} 条答案")

    answers, invalid = {}, []
    for q in questions:
        val = validate_answer(q, raw.get(q["qid"]) or raw.get(str(q["qid"])))
        if val:
            answers[q["qid"]] = val
        else:
            invalid.append(q["qid"])
            answers[q["qid"]] = ""
    log(f"有效答案 {len(answers) - len(invalid)} 题，无效 {len(invalid)} 题")

    afile = os.path.join(ROOT, f"answers-{work_id}.json")
    with open(afile, "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False, indent=1)

    result = {"workId": work_id, "answered": len(answers) - len(invalid),
              "invalid": invalid, "answersFile": afile}

    if save:
        st = os.path.join(work_dir(work_id), "state.json")
        r = run_script([os.path.join(ROOT, "lxt.py"), "save",
                        "--state", st, "--answers", afile])
        log(r["out"].strip()[:500])
        result["save"] = r

    if submit:
        st = os.path.join(work_dir(work_id), "state.json")
        r = run_script([os.path.join(ROOT, "lxt.py"), "submit",
                        "--state", st, "--answers", afile])
        log(r["out"].strip()[:500])
        result["submit"] = r

    return result


# ---------------------------------------------------------------- 任务系统

JOBS: Dict[str, dict] = {}
JOB_LOCK = threading.Lock()


def new_job(kind: str, title: str) -> str:
    jid = uuid.uuid4().hex[:12]
    with JOB_LOCK:
        JOBS[jid] = {"id": jid, "kind": kind, "title": title, "status": "running",
                     "log": [], "result": None, "error": None,
                     "started": time.strftime("%H:%M:%S")}
    return jid


def job_log(jid: str, msg: str):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j["log"].append(msg)


def job_done(jid: str, result=None, error=None):
    with JOB_LOCK:
        j = JOBS.get(jid)
        if j is None:
            return
        j["status"] = "error" if error else "done"
        j["result"] = result
        j["error"] = error


def run_job_async(jid: str, fn):
    def wrapper():
        try:
            fn()
        except Exception as e:          # noqa: BLE001
            job_log(jid, f"❌ {e}")
            job_done(jid, error=str(e))
    threading.Thread(target=wrapper, daemon=True).start()


# ---------------------------------------------------------------- 模型

class JobReq(BaseModel):
    action: str                       # list | resolve | answer | save | submit | auto
    workIds: List[str] = []
    answers: Optional[Dict[str, str]] = None
    submit: bool = False
    save: bool = True


class ConfigReq(BaseModel):
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    auto_submit: Optional[bool] = None


# ---------------------------------------------------------------- 路由

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/config")
def get_config():
    cfg = load_config()
    key = cfg["llm"].get("api_key", "")
    cfg["llm"]["api_key_masked"] = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
    cfg["llm"]["api_key"] = "" if key else ""
    cfg["llm"]["has_key"] = bool(key)
    return cfg


@app.post("/api/config")
def set_config(req: ConfigReq):
    cfg = load_config()
    if req.llm_base_url is not None:
        cfg["llm"]["base_url"] = req.llm_base_url.strip()
    if req.llm_model is not None:
        cfg["llm"]["model"] = req.llm_model.strip()
    if req.llm_api_key is not None and req.llm_api_key.strip():
        cfg["llm"]["api_key"] = req.llm_api_key.strip()
    if req.auto_submit is not None:
        cfg["auto_submit"] = bool(req.auto_submit)
    save_config(cfg)
    return {"ok": True}


@app.post("/api/testllm")
def test_llm():
    cfg = load_config()
    if not cfg["llm"].get("api_key"):
        return JSONResponse({"ok": False, "msg": "还没配置 API Key"}, status_code=400)
    try:
        t0 = time.time()
        out = call_llm('只输出这个 JSON，不要别的：{"ok":1}', cfg)
        return {"ok": True, "latency": round(time.time() - t0, 2),
                "model": cfg["llm"]["model"], "reply": out}
    except Exception as e:            # noqa: BLE001
        return JSONResponse({"ok": False, "msg": str(e)[:400]}, status_code=400)


class BenchReq(BaseModel):
    workId: str
    models: List[str] = []
    temperature: Optional[float] = None


@app.post("/api/bench")
def bench(req: BenchReq):
    """用已知答案给模型打分（不保存、不提交）。"""
    cfg = load_config()
    if not cfg["llm"].get("api_key"):
        raise HTTPException(400, "还没配置 API Key")

    qfile = os.path.join(work_dir(req.workId), "questions.json")
    questions = load_json(qfile)
    if not questions:
        raise HTTPException(404, f"{req.workId} 还没抓题")

    ref_file = os.path.join(ROOT, f"answers-{req.workId}.json")
    ref = load_json(ref_file, {}) or {}
    ref = {k: v for k, v in ref.items() if not k.startswith("_") and v}
    if not ref:
        raise HTTPException(404, "这份作业没有可作为基准的答案文件")

    models = req.models or [cfg["llm"]["model"]]
    prompt = build_prompt(questions)
    out = []

    for model in models:
        c = json.loads(json.dumps(cfg))
        c["llm"]["model"] = model
        if req.temperature is not None:
            c["llm"]["temperature"] = req.temperature

        entry = {"model": model}
        try:
            t0 = time.time()
            raw = call_llm(prompt, c)
            entry["latency"] = round(time.time() - t0, 1)
        except Exception as e:                      # noqa: BLE001
            entry.update({"ok": False, "error": str(e)[:300]})
            out.append(entry)
            continue

        stat = {"total": 0, "right": 0, "byType": {}, "wrong": []}
        for q in questions:
            qid = q["qid"]
            if qid not in ref:
                continue
            want = ref[qid]
            got = validate_answer(q, raw.get(qid) or raw.get(str(qid))) or ""
            stat["total"] += 1
            t = q["type"]
            d = stat["byType"].setdefault(t, {"total": 0, "right": 0})
            d["total"] += 1
            if got == want:
                stat["right"] += 1
                d["right"] += 1
            else:
                stat["wrong"].append({
                    "qid": qid, "type": t, "want": want, "got": got,
                    "stem": q["stem"][:60],
                })

        entry.update({
            "ok": True,
            "accuracy": round(stat["right"] / stat["total"] * 100, 1) if stat["total"] else 0,
            "right": stat["right"], "total": stat["total"],
            "byType": {k: f"{v['right']}/{v['total']}" for k, v in stat["byType"].items()},
            "wrong": stat["wrong"][:15],
            "wrongCount": len(stat["wrong"]),
        })
        out.append(entry)

    return {"workId": req.workId, "reference": len(ref), "results": out}


@app.get("/api/works")
def get_works(refresh: bool = True):
    if refresh:
        r = refresh_worklist()
        if r["code"] != 0:
            raise HTTPException(500, f"刷新作业列表失败: {r['out'][:300]}")
    wl = read_worklist()
    for w in wl:
        w["parsed"] = os.path.exists(os.path.join(work_dir(w["workId"]), "questions.json"))
        w["answered"] = os.path.exists(os.path.join(ROOT, f"answers-{w['workId']}.json"))
    return {"count": len(wl), "works": wl}


@app.get("/api/questions/{work_id}")
def get_questions(work_id: str):
    q = load_json(os.path.join(work_dir(work_id), "questions.json"))
    if q is None:
        raise HTTPException(404, "还没抓题")
    return {"workId": work_id, "count": len(q), "questions": q}


@app.get("/api/answers/{work_id}")
def get_answers(work_id: str):
    a = load_json(os.path.join(ROOT, f"answers-{work_id}.json"), {}) or {}
    return {"workId": work_id, "answers": a}


@app.post("/api/jobs")
def create_job(req: JobReq):
    cfg = load_config()

    if req.action == "list":
        jid = new_job("list", "刷新作业列表")

        def fn():
            r = refresh_worklist()
            job_log(jid, r["out"].strip()[:800])
            job_done(jid, {"code": r["code"]})
        run_job_async(jid, fn)
        return {"jobId": jid}

    if not req.workIds:
        raise HTTPException(400, "缺少 workIds")

    titles = {w["workId"]: w.get("title", "") for w in read_worklist()}
    jid = new_job(req.action, f"{req.action} · {','.join(req.workIds)}")

    def fn():
        results = []
        for wid in req.workIds:
            job_log(jid, f"=== {wid} {titles.get(wid, '')} ===")
            if req.action == "resolve":
                r = run_script([os.path.join(ROOT, "batch_resolve.py"), "--ids", wid])
                job_log(jid, r["out"].strip()[-1200:])
                results.append({"workId": wid, **r})
            elif req.action == "answer":
                res = answer_work(wid, cfg, save=req.save, submit=False, log=lambda s: job_log(jid, s))
                results.append(res)
            elif req.action == "auto":
                # 先抓题（若没抓过），再作答
                if not os.path.exists(os.path.join(work_dir(wid), "questions.json")):
                    job_log(jid, "未解析，先抓题…")
                    r = run_script([os.path.join(ROOT, "batch_resolve.py"), "--ids", wid])
                    job_log(jid, r["out"].strip()[-800:])
                    if r["code"] != 0:
                        results.append({"workId": wid, "error": "抓题失败"})
                        continue
                res = answer_work(wid, cfg, save=True, submit=req.submit,
                                  log=lambda s: job_log(jid, s))
                results.append(res)
            elif req.action == "save":
                st = os.path.join(work_dir(wid), "state.json")
                af = os.path.join(ROOT, f"answers-{wid}.json")
                if req.answers:
                    with open(af, "w", encoding="utf-8") as f:
                        json.dump(req.answers, f, ensure_ascii=False, indent=1)
                r = run_script([os.path.join(ROOT, "lxt.py"), "save",
                                "--state", st, "--answers", af])
                job_log(jid, r["out"].strip()[:600])
                results.append({"workId": wid, **r})
            elif req.action == "verify":
                st = os.path.join(work_dir(wid), "state.json")
                r = run_script([os.path.join(ROOT, "lxt.py"), "verify", "--state", st])
                job_log(jid, r["out"].strip()[:800])
                results.append({"workId": wid, **r})
            elif req.action == "submit":
                st = os.path.join(work_dir(wid), "state.json")
                af = os.path.join(ROOT, f"answers-{wid}.json")
                r = run_script([os.path.join(ROOT, "lxt.py"), "submit",
                                "--state", st, "--answers", af])
                job_log(jid, r["out"].strip()[:600])
                results.append({"workId": wid, **r})
            else:
                raise RuntimeError(f"未知动作 {req.action}")
        job_done(jid, results)

    run_job_async(jid, fn)
    return {"jobId": jid}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with JOB_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(404, "任务不存在")
        return json.loads(json.dumps(j, ensure_ascii=False))


def _port_in_use(host: str, port: int) -> bool:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        # 能连上说明已有服务在监听
        s.settimeout(1.0)
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _open_browser(url: str, delay: float = 1.2):
    if os.environ.get("LXT_NO_BROWSER") == "1":
        return
    def go():
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, go).start()


def main():
    import uvicorn
    cfg = load_config()
    port = int(os.environ.get("LXT_PANEL_PORT") or cfg.get("port", 8765))
    host = os.environ.get("LXT_PANEL_HOST") or cfg.get("host", "127.0.0.1")
    token = (cfg.get("access_token") or "").strip()

    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{port}/" + (f"?token={token}" if token else "")

    # 端口已被占用：多半是面板已经在跑，直接打开浏览器即可
    if _port_in_use(host, port):
        print(f"📌 {port} 端口已有服务在运行，看起来面板已经启动了。")
        print(f"   直接打开: {url}")
        _open_browser(url, delay=0.3)
        return

    print(f"面板地址: {url}")
    print(f"API 文档: http://{shown}:{port}/docs")

    if host in ("0.0.0.0", "::"):
        if not token:
            print("\n⚠️  警告：已监听所有网卡（局域网可访问），但没有设置 access_token！")
            print("    任何人只要连上同一网络，就能操作你的学习通账号、消耗你的 API Key。")
            print("    强烈建议在 panel/config.json 里设置 access_token 后重启。\n")
        else:
            print(f"🔒 局域网模式：访问需带口令\n")

    if token:
        print("🔑 已启用访问口令（/api/* 需要 X-Access-Token 或 ?token=）")

    print("正在启动服务，稍后会自动打开浏览器…（不想自动打开可设 LXT_NO_BROWSER=1）")
    _open_browser(url, delay=1.8)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
