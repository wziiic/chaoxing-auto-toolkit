# 学习通作业自动化工具包（抓题 / LLM 作答 / 面板 + API）

把「学习通（超星）作业」这条流程自动化：**列出作业 → 自动获取作业链接 → 解析题目 → 调 LLM 作答 → 暂存 → 核对 → 交卷**，
并提供一个本地网页面板和 HTTP API，不用每次都开对话。

> ⚠️ **仅供个人学习辅助**。作业代做的学术诚信后果由使用者自负；请遵守所在学校的规章制度。
> 本仓库不包含任何账号凭据，使用时凭据只留在本地。

## 特性

- **自动获取作业链接**：不用手工复制 `dowork` 链接（该链接是一次性会话令牌，手工模式每次都要重取）
- **桌面端会话复用**：自动从学习通桌面端（Electron）解密 Cookie，无需手填
- **真实浏览器补会话**：`work/task` 需要完整网页登录态，用 Playwright + 本机 Chrome 跑一遍 SSO 即可补齐
- **LLM 自动作答**：兼容 OpenAI 协议（DeepSeek / 通义 / Kimi / 本地 Ollama）
- **图形面板 + REST API**：批量抓题、批量作答、看答案、核对、交卷
- **默认只暂存不交卷**，交卷需二次确认

## 安装

```bash
git clone <this-repo>
cd <repo>
pip install -r requirements.txt
python -m playwright install   # 可选：若用本机已装 Chrome，可不装（脚本用 channel="chrome"）
```

前置条件：

1. Windows（脚本用到 DPAPI 解密与 Chrome 路径）
2. 已安装并**登录**学习通桌面端（`%APPDATA%\cxstudy` 会被读取）
3. 本机有 Chrome

## 快速开始

```bash
# 1. 从桌面端提取 Cookie
python grab_cookie.py

# 2. 列出全部作业
python list_works.py

# 3. 启动面板
双击 启动面板.bat
# 或
python panel/app.py
```

打开 http://127.0.0.1:8765/ ，在「LLM 设置」里填 API Key（或复制 `panel/config.example.json` 为 `panel/config.json` 再填）。

## 分享给别人用：对方需要准备什么

### 只需要填一样东西

| 要填的 | 填在哪 | 说明 |
|---|---|---|
| **LLM API Key** | 面板「LLM 设置」，或 `panel/config.json` 的 `llm.api_key` | 唯一必填项。用官方 DeepSeek 时模型填 `deepseek-chat` |

### 不需要填的（会自动获取）

| 项目 | 为什么不用填 |
|---|---|
| Cookie / 登录凭据 | `grab_cookie.py` 从**对方自己电脑上**的桌面端解密 |
| 课程 ID、班级 ID、作业 ID | `list_works.py` 自动列出 |
| 一次性 `dowork` 链接 | `batch_resolve.py` 用浏览器自动生成 |
| 端口 | 默认 8765，冲突才改 |

### 对方必须满足的环境前提

1. **Windows**（脚本依赖 DPAPI 与 Chrome 路径）
2. **Python 3.9+** 且在 PATH
3. **Chrome 已安装**
4. **学习通桌面端已安装并登录过至少一次** ← 最关键，Cookie 从这里来
5. 能联网访问 chaoxing.com 与所选 LLM 服务

### 对方上手三步

```bash
pip install -r requirements.txt
python doctor.py          # 自检：哪项不满足会直接提示怎么修
# 然后双击 启动面板.bat
```

`doctor.py` 会逐项检查 Python / 依赖 / Chrome / 桌面端 / Cookie 解密 / LLM 配置 / 端口，并给出修复建议。

### 常见不适配情况

| 现象 | 原因 | 处理 |
|---|---|---|
| 提示找不到 `%APPDATA%\cxstudy` | 桌面端是别的发行版，或从未登录 | 登录一次；若目录名不同，改 `lxt.py` 里 `load_cookies_for_browser` 的路径 |
| Cookie 解出 0 条 / 缺 `_uid` | 桌面端处于未登录状态 | 重新登录桌面端再跑 |
| `work/task` 被弹登录页 | 网页端会话未建立 | 属正常，走 `batch_resolve.py`（浏览器会自动补 SSO） |
| 提示 `--remote-debugging-port: bad option` | 桌面端有参数校验 | 正常现象，本方案不依赖 CDP，无需处理 |
| 自动作答报模型不存在 | 用了官方 API 却填了内部模型名 | 改为 `deepseek-chat` 或 `deepseek-reasoner` |

> 每人必须用**自己的账号**在自己电脑上跑，不能共用别人的 `cookie.txt`——那是账号钥匙。

## 实测表现

以「党史章节测试 50 题」（已确认满分 100）为基准做的模型对比：

| 模型 | 准确率 | 耗时 | 单选 | 多选 | 判断 |
|---|---|---|---|---|---|
| `deepseek-chat` | 98% (49/50) | 1.8s | 28/28 | 11/12 | 10/10 |
| `deepseek-reasoner` | 98% (49/50) | 24.5s | 28/28 | 11/12 | 10/10 |

结论：**`deepseek-chat` 够用且快**，`reasoner` 没有准确率收益。多选题是唯一薄弱环节，建议交卷前用「看答案」人工扫一眼。

面板自带「模型基准测试」，可以拿任意已作答的作业当基准给新模型打分。

## 目录结构

```
lxt.py                 主程序：fetch / parse / loadhtml / save / submit / verify
grab_cookie.py         从桌面端解密 Cookie（DPAPI + AES-GCM）
list_works.py          作业清单
batch_resolve.py       批量取作业页 + 解析（Playwright + Chrome）
resolve_work.py        单份取作业页
show_work.py           打印题目
check_all.py           批量核对已保存答案
panel/app.py           面板后端（FastAPI）
panel/static/index.html 面板前端
panel/config.example.json 配置模板
启动面板.bat            一键启动
```

## 安全说明

以下文件**已被 `.gitignore` 排除**，请勿手动提交：

- `cookie.txt`：等于你的学习通登录凭据
- `panel/config.json`：含 LLM API Key
- `works/`、`answers-*.json`、`worklist.json`、`questions.json`：含你的课程与作业内容

如果怀疑泄露：学习通「退出登录」刷新会话；LLM Key 去平台后台吊销重发。

---

## 使用方法（详细）

这套流程已经跑通：《智科与人工智能学科导论》40题、心理健康3份、生成式AI 9章、近现代史2份、党史章节测试。

## 零、图形面板 + API（日常用这个）

双击 `启动面板.bat`，或手动：

```bash
python panel/app.py
```

浏览器打开 **http://127.0.0.1:8765/**

面板能做的事：列作业 → 勾选 → 批量抓题 → 批量自动作答（调 LLM）→ 看答案 → 核对 → 交卷。

首次使用要在面板顶部「LLM 设置」里填 **API Key**（兼容 OpenAI 协议：DeepSeek / 通义 / Kimi / 本地 Ollama 都行）。
不填 Key 就只能抓题、看题、手动保存，不能自动作答。

### HTTP API

交互式文档：**http://127.0.0.1:8765/docs**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/works?refresh=true` | 作业清单（含状态/是否已抓题/是否已作答） |
| GET | `/api/questions/{workId}` | 题目结构 |
| GET | `/api/answers/{workId}` | 当前答案 |
| GET | `/api/config` | 读取配置（Key 打码） |
| POST | `/api/config` | 改配置：`{"llm_base_url","llm_model","llm_api_key"}` |
| POST | `/api/jobs` | 提交异步任务，返回 `jobId` |
| GET | `/api/jobs/{jobId}` | 查进度与日志 |

`POST /api/jobs` 的 `action` 取值：

```json
{"action":"list"}                                  // 刷新列表
{"action":"resolve","workIds":["55366359"]}        // 只抓题
{"action":"auto","workIds":["55366359"],"submit":false}  // 抓题+LLM作答+暂存
{"action":"auto","workIds":["55366359"],"submit":true}   // 作答并交卷
{"action":"save","workIds":["55366359"],"answers":{"qid":"A"}}  // 自定义答案保存
{"action":"verify","workIds":["55366359"]}         // 回读核对
{"action":"submit","workIds":["55366359"]}         // 交卷
```

curl 例子：

```bash
curl -X POST http://127.0.0.1:8765/api/jobs -H "Content-Type: application/json" \
     -d "{\"action\":\"auto\",\"workIds\":[\"56511284\"]}"
```

## 一、脚本模式（无人值守 / 排错用）

```
list_works.py       列出所有作业（标题/课程/状态/入口链接）
      ↓
resolve_work.py     用真实 Chrome 打开入口，自动完成 SSO 跳转，拿到有效 dowork 页
      ↓              同时导出浏览器会话 Cookie → cookie.txt
parse               解析题目（题干/选项/真实答案键）
      ↓
（我判答案，写成 answers-<workId>.json）
      ↓
save                暂存答案（不交卷）
verify              回读核对
```

用法：

```bash
cd <workspace>
# 1) 列出所有作业，拿 url
python list_works.py

# 2) 批量取某一批作业的题目（一次浏览器会话搞定多份）
python batch_resolve.py --ids 55366359,55366922,56511284
python batch_resolve.py --all-undone

# 3) 单份也可
python resolve_work.py --task "<url>"

# 4) 看某份作业的题目
python show_work.py <workId>

# 5) 写答案 → 暂存 → 核对（逐份 state 独立）
python lxt.py save   --state works/<workId>/state.json --answers answers-<workId>.json
python lxt.py verify --state works/<workId>/state.json

# 6) 一次性核对全部
python check_all.py
```

`batch_resolve.py` 会把每份作业拆到 `works/<workId>/`（page.html / state.json / questions.json），
互不干扰，可以逐份作答、逐份保存。

## 二、为什么必须用浏览器

`work/task` 与 `dowork` 需要**网页端登录会话**，不是单纯 Cookie 能凑齐的：

- 桌面端的 Cookie 只够访问 `all-task` 列表和课程门户
- 直接请求 `work/task` 会被弹到 `passport2` 登录页（实测 4 种 Cookie 组合均失败）
- 只有真实浏览器执行 JS 才会完成 SSO 自动登录，落回作业页

所以 `resolve_work.py` 走的是「Playwright + 本机 Chrome + 注入桌面端 Cookie」，
让浏览器自己把会话补齐，然后把**新会话 Cookie 导出**给后续请求复用。

### 桌面端 Cookie 从哪来

`grab_cookie.py` / `resolve_work.py` 解析 Electron 的 Chromium 存储：

```
%APPDATA%\cxstudy\Local State        -> os_crypt.encrypted_key
    ↓ DPAPI(CryptUnprotectData)
AES-256 主密钥
    ↓
%APPDATA%\cxstudy\Network\Cookies    -> encrypted_value (v10/v11)
    ↓ AES-256-GCM 解密
Cookie 明文
```

顺带记录：桌面端的 `cxstudy.exe` 有自定义参数校验，**无法用 `--remote-debugging-port` 开 CDP**，
所以不要尝试接管它的 webview，直接用 Playwright 另开 Chrome 更省事。

## 三、手工模式（备用，链接失效时）

```
你从浏览器复制一条当前有效的 dowork 链接
   ↓
fetch → parse → 判答案 → save → verify
```

`dowork` 链接的 `answerId/enc` 是一次性会话令牌，用过就失效，所以手工模式每次都要新链接。

## 关键机制（三个坑）

### 坑1：选项是乱序的

```html
<!-- aria-label 前缀 B = 真实键；data="B" = 真实键；屏幕上显示的是 A -->
<div aria-label="B 遗传算法选择" qid="215411695" qtype="0">
    <span data="B">A</span>
    <div class="answer_p">遗传算法</div>
</div>
```

- `data` = **真实答案键**（提交用这个）
- span 文字 = 学生屏幕上的 **display 字母**（乱序后）

`parse` 会把两者都打出来（`data=X 屏幕=Y`），别搞混。

### 坑2：判断题的 data 不是字母

判断题 `data="true"` / `data="false"`，答案就写 `true` / `false`。

### 坑3：DOM 序列化会改属性

浏览器拿到的 HTML（`page.content()`）与服务器原始 HTML 不同：

- `typeName` → `typename`（小写）
- `aria-label='...'`（单引号）→ `aria-label="..."`（双引号）

解析器对两者都做了兼容，改解析逻辑时注意。

## 暂存 ≠ 交卷

- `save` → `pyFlag=1`，可反复覆盖，**不交卷**
- `submit` → `pyFlag` 为空 + `/work/validate`，**会锁定**

默认永远先 `save`，让用户复核后手动提交。

## 答案文件格式

```json
{
  "406140648": "ABCD",     // 多选：把真实键连起来
  "406140643": "D",        // 单选：一个真实键
  "406140654": "true",     // 判断题：true / false
  "406140650": ""          // 留空 = 不作答
}
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `lxt.py` | 主程序（fetch/parse/loadhtml/save/submit/verify） |
| `list_works.py` | 列出全部作业及入口链接 |
| `resolve_work.py` | 浏览器自动取作业页 + 导出会话 |
| `grab_cookie.py` | 从桌面端提取 Cookie |
| `dump_cookies.py` | 按域名打印桌面端全部 Cookie（排查用） |
| `cookie.txt` | 当前会话 Cookie |
| `worklist.json` | 作业清单缓存 |
| `state.json` | 表单参数缓存 |
| `answers-<workId>.json` | 各作业答案 |

## 安全提醒

- `cookie.txt` 等于账号钥匙，别外传；用完可在学习通「退出登录」刷新会话
- 作业/测验代做属学术诚信范畴，最终是否提交由你决定
- 建议：**我暂存 → 你浏览器逐题复核 → 你自己点提交**
