"""List course titles quickly; resolve only the selected course entry."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from study_core import Interrupted, StudyError, course_link, digest


LIST_URL = "https://mooc2-ans.chaoxing.com/visit/courses/list"
CARD_SCRIPT = """() => Array.from(document.querySelectorAll('li.course')).flatMap(card => {
 const link=card.querySelector('a[href*="/visit/stucoursemiddle"]');
 if(!link)return [];
 const title=Array.from(card.querySelectorAll('.course-name,a[href*="/visit/stucoursemiddle"]')).map(e=>(e.getAttribute('title')||e.innerText||'').trim()).find(Boolean);
 return [{title:title||'未命名课程',entry:link.href}];
})"""


def platform_url(value, paths=None):
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and parts.hostname and parts.hostname.endswith(".chaoxing.com")
                and parts.port in (None, 443) and not parts.username and not parts.password
                and (paths is None or parts.path in paths))
    except ValueError:
        return False


def progress_state(text):
    matches = set(re.findall(r"已完成任务点\s*[:：]\s*(\d+)\s*[/／]\s*(\d+)", text))
    if len(matches) != 1:
        return {"platform": "unknown", "completed": None, "total": None, "reason": "平台未提供明确的课程任务完成数"}
    completed, total = map(int, matches.pop())
    if total <= 0 or completed > total:
        return {"platform": "unknown", "completed": completed, "total": total, "reason": "任务总数为零或进度数据异常，不能确认课程完成状态"}
    return {"platform": "todo" if completed < total else "done", "completed": completed, "total": total,
            "reason": "" if completed < total else "平台显示全部任务点已完成"}


def page_study_link(frame):
    """Fallback for the known native toOld navigation, using only this page's values."""
    data = frame.evaluate("""() => ({onclick:document.querySelector('.chapter_item[onclick^="toOld("]')?.getAttribute('onclick')||'',
        source:typeof toOld==='function'?toOld.toString():'',domain:typeof ServerHost!=='undefined'?ServerHost.mooc1Domain:'',
        enc:document.querySelector('#enc')?.value||'',cpi:document.querySelector('#cpi')?.value||''})""")
    args = re.search(r"^toOld\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*,\s*(\d+)\s*\)", data["onclick"])
    if not args or "/mycourse/studentstudy?chapterId=" not in data["source"] or not platform_url(data.get("domain", "")):
        return ""
    enc = data["enc"] or next(iter(re.findall(r"\bvar\s+enc\s*=\s*['\"]([^'\"]+)['\"]", data["source"])), "")
    cpi = data["cpi"] or next(iter(re.findall(r"\bvar\s+cpi\s*=\s*['\"]?(\d+)", data["source"])), "")
    version = re.search(r"\bvar\s+fanyaVersion\s*=\s*(\d+)", data["source"])
    if not enc or not re.fullmatch(r"\d+", cpi) or not version:
        return ""
    course, chapter, clazz, hidetype = args.groups()
    return data["domain"].rstrip("/") + "/mycourse/studentstudy?" + urlencode({"chapterId": chapter,
        "courseId": course, "clazzid": clazz, "cpi": cpi, "enc": enc, "mooc2": 1,
        "hidetype": hidetype, "fanyaVersion": version.group(1)})


def check_access(page):
    for frame in page.frames:
        try:
            blocked = frame.evaluate("""() => ({login:!!document.querySelector('input[type="password"]'),
                verify:/验证码|安全验证/.test(document.title)||/请完成验证|访问过于频繁|异常访问|身份验证后/.test(document.body?.innerText||'')})""")
        except Exception:
            continue
        if blocked["login"] or "passport" in (urlsplit(frame.url).hostname or ""):
            raise StudyError("登录已失效，请在课程窗口登录或重新登录学习通桌面端后重试")
        if blocked["verify"]:
            raise StudyError("平台要求验证码或身份验证，已停止读取；请先在课程窗口处理")


def discover_courses(driver, publish, match_course=None, selected_entry=None):
    from playwright.sync_api import TimeoutError as NavigationTimeout
    page = driver.context.new_page()
    page.set_default_timeout(4000)
    page.on("dialog", lambda dialog: dialog.dismiss())
    entries, courses, seen = [], [], set()
    complete = True
    try:
        if selected_entry is not None:
            if not platform_url(selected_entry.get("entry", ""), {"/visit/stucoursemiddle"}):
                raise StudyError("课程入口无效，请刷新课程列表")
            entries = [selected_entry]
        else:
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=25000)
            driver.checkpoint()
            check_access(page)
            # Include lazy-loaded cards and native pagination, with a finite upper bound.
            for number in range(50):
                driver.checkpoint()
                page.wait_for_timeout(300)
                cards = page.evaluate(CARD_SCRIPT)
                added = 0
                for card in cards:
                    if not platform_url(card["entry"], {"/visit/stucoursemiddle"}):
                        continue
                    query = {key.lower(): values[0] for key, values in parse_qs(urlsplit(card["entry"]).query).items()}
                    if match_course and any(query.get(key) != match_course.get(key) for key in ("courseid", "clazzid")):
                        continue
                    if not all(re.fullmatch(r"\d+", query.get(key, "")) for key in ("courseid", "clazzid")):
                        continue
                    identity = (query["courseid"], query["clazzid"], query.get("cpi", ""))
                    if identity not in seen:
                        seen.add(identity)
                        card["id"] = digest(identity)[:24]
                        entries.append(card)
                        added += 1
                page.evaluate("() => window.scrollTo(0,document.body.scrollHeight)")
                page.wait_for_timeout(400)
                if match_course and entries:
                    break
                if len(page.evaluate(CARD_SCRIPT)) > len(cards):
                    continue
                next_page = page.get_by_text("下一页", exact=True)
                enabled = [item for item in next_page.all() if item.is_visible() and item.evaluate("e=>!e.closest('[disabled],[aria-disabled=true],.disabled')")]
                if not enabled:
                    break
                if (not added and not match_course) or number == 49:
                    complete = False
                    break
                enabled[0].click()
                page.wait_for_timeout(600)
                check_access(page)
        if not entries:
            raise StudyError("未读取到可访问的学生课程；请确认学习通已登录，或使用手动链接")
        if selected_entry is None and match_course is None:
            courses = [{"id": item["id"], "title": item["title"], "entry": item["entry"],
                        "url": "", "platform": "unread", "completed": None, "total": None, "reason": ""}
                       for item in entries]
            result = {"courses": courses, "checked": len(courses), "total": len(courses), "complete": complete,
                      "message": "已读取课程标题；选择课程后读取目录，开始学习后再识别当前章节任务"}
            publish(**result)
            return result
        publish(courses=[], checked=0, total=len(entries), complete=complete, message="正在获取所选课程入口…")
        for index, entry in enumerate(entries):
            driver.checkpoint()
            item = {"id": entry["id"], "title": entry["title"], "url": "", "platform": "unknown",
                    "completed": None, "total": None, "reason": ""}
            failure_reason = "课程入口未能加载，请刷新列表或手动粘贴链接"
            try:
                try:
                    page.goto(entry["entry"], wait_until="domcontentloaded", timeout=20000)
                except NavigationTimeout:
                    check_access(page)
                    expected_entry = {key.lower(): values[0] for key, values in parse_qs(urlsplit(entry['entry']).query).items()}
                    current = {key.lower(): values[0] for key, values in parse_qs(urlsplit(page.url).query).items()}
                    if (not platform_url(page.url) or any(current.get(key)!=expected_entry.get(key) for key in ('courseid','clazzid'))
                            or not page.get_by_text('章节', exact=True).count()):
                        raise
                check_access(page)
                driver.checkpoint()
                failure_reason = "无法打开课程章节目录，请在课程窗口检查访问权限"
                page.get_by_text("章节", exact=True).click(timeout=5000)
                chapter = None
                for attempt in range(75):
                    driver.checkpoint()
                    check_access(page)
                    chapter = next((frame for frame in page.frames if urlsplit(frame.url).path.endswith("/mycourse/studentcourse")), None)
                    if chapter and chapter.locator(".chapter_item").count():
                        break
                    page.wait_for_timeout(200)
                if not chapter or not chapter.locator(".chapter_item").count():
                    raise ValueError("章节目录尚未加载")
                # Progress HTML can arrive before the deferred navigation
                # scripts. A chapter item alone does not mean toOld can run.
                failure_reason = "章节入口脚本尚未加载完成，请稍后刷新列表；未读取任务点"
                entry_ready = False
                for attempt in range(150):
                    driver.checkpoint()
                    check_access(page)
                    entry_ready = chapter.evaluate("""() => !!document.querySelector('a[href*="/mycourse/studentstudy"]') ||
                        (typeof toOld==='function' && !!document.querySelector('.chapter_item[onclick^="toOld("]'))""")
                    if entry_ready:
                        break
                    page.wait_for_timeout(200)
                if not entry_ready:
                    raise ValueError("章节入口脚本未就绪")
                captured = []
                failure_reason = "平台未提供可打开的章节或有效学习链接，可刷新列表或手动粘贴链接"
                expected = {key.lower(): values[0] for key, values in parse_qs(urlsplit(entry["entry"]).query).items()}

                def accept(candidate):
                    try:
                        course_link(candidate)
                    except StudyError:
                        return False
                    actual = {key.lower(): values[0] for key, values in parse_qs(urlsplit(candidate).query).items()}
                    if any(actual.get(key) != expected.get(key) for key in ("courseid", "clazzid")):
                        return False
                    captured.append(candidate)
                    return True

                def capture(route):
                    candidate = route.request.url
                    parts = urlsplit(candidate)
                    if parts.path == "/mycourse/transfer":
                        candidate = parse_qs(parts.query).get("refer", [""])[0]
                    accept(candidate)
                    route.abort()

                pattern = re.compile(r"^https://[^/]+\.chaoxing\.com/mycourse/(?:transfer|studentstudy)(?:\?|$)")
                existing_pages = set(driver.context.pages)
                # The first request of a native window.open is not intercepted
                # by page.route. This read-only context is idle except for the
                # temporary directory page, and all study navigation is aborted.
                driver.context.route(pattern, capture)
                try:
                    links = chapter.locator('a[href*="/mycourse/studentstudy"]')
                    for candidate in links.evaluate_all("nodes=>nodes.map(e=>e.href)"):
                        if accept(candidate):
                            break
                    if not captured:
                        accept(page_study_link(chapter))
                    if not captured:
                        # Let the platform generate its own signed URL, but abort before navigation.
                        try:
                            indexes = chapter.locator('.chapter_item[onclick]').evaluate_all(
                                r"nodes=>nodes.flatMap((e,i)=>/^\s*toOld\s*\(/.test(e.getAttribute('onclick'))?[i]:[])")
                            if indexes:
                                chapter.locator('.chapter_item[onclick]').nth(indexes[0]).click(timeout=4000, no_wait_after=True)
                        except Interrupted:
                            raise
                        except Exception:
                            pass  # The click can time out while its native URL is still being generated.
                        for attempt in range(50):
                            driver.checkpoint()
                            if captured:
                                break
                            page.wait_for_timeout(100)
                finally:
                    driver.context.unroute(pattern, capture)
                    for popup in set(driver.context.pages) - existing_pages:
                        popup.close()
                if not captured:
                    accept(page_study_link(chapter))
                if captured:
                    item["url"] = captured[0]
                else:
                    item["reason"] = "未取得有效的学习页链接，请使用手动链接"
            except (StudyError, Interrupted):
                raise
            except Exception:
                item["reason"] = failure_reason
            courses.append(item)
            publish(courses=courses, checked=index + 1, total=len(entries), complete=complete,
                    message=f"已核对 {index + 1} / {len(entries)} 门课程")
        return {"courses": courses, "checked": len(entries), "total": len(entries), "complete": complete,
                "message": "已获取所选课程入口，尚未读取任务点" if complete else "课程列表可能未加载完整，请刷新重试；当前仅展示已读取的课程"}
    finally:
        page.close()
