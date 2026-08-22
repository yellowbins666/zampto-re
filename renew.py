#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import re
import requests
from seleniumbase import SB
from datetime import datetime

# ============================================================
# 环境变量
# ============================================================

EMAIL = os.environ.get("ZAM_PTO_EMAIL", "").strip()
PASSWORD = os.environ.get("ZAM_PTO_PASSWORD", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()

IS_PROXY = os.environ.get("IS_PROXY", "true").strip().lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER", "http://127.0.0.1:1081").strip()

BASE_URL = "https://dash.zampto.net"
EMAIL_SELECTOR = "#email"
PASSWORD_SELECTOR = "#password"

# ============================================================
# Telegram 通知
# ============================================================

def send_tg_message(status_icon: str, status_text: str, detail: str = ""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return

    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    if "@" in EMAIL:
        name, domain = EMAIL.split("@", 1)
        masked_email = f"{name[:2]}****{name[-2:]}@{domain}" if len(name) > 4 else f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + "****" if EMAIL else "未配置"

    text = (
        f"🇫🇷 ZamPTO 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 账户: {masked_email}\n"
        f"⏱️ 时间: {current_time}"
    )
    if detail:
        text += f"\n📝 详情: {detail[:800]}"

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
        if r.ok:
            print("📩 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 通知发送失败: HTTP {r.status_code}")
    except Exception as e:
        print(f"⚠️ Telegram 通知发送异常: {e}")

# ============================================================
# 喵酱的无敌 Cloudflare 穿透打勾模块
# ============================================================

def _turnstile_token_ready(sb) -> bool:
    try:
        token_ok = sb.execute_script("""
            var inp = document.querySelector("input[name='cf-turnstile-response']");
            return inp && inp.value && inp.value.length > 20;
        """)
        if token_ok: return True
    except Exception: pass

    try:
        success_visible = sb.execute_script("""
            var s = document.getElementById('success');
            if (!s) return false;
            var style = window.getComputedStyle(s);
            return style.display !== 'none' && style.visibility !== 'hidden';
        """)
        if success_visible: return True
    except Exception: pass
    return False

def _try_click_turnstile(sb) -> bool:
    try: sb.uc_gui_click_captcha()
    except Exception: pass

    try:
        if sb.is_element_present("iframe[src*='challenges.cloudflare']"):
            sb.switch_to_frame("iframe[src*='challenges.cloudflare']")
            sb.click("input[type='checkbox'], .cb-lb, .mark", timeout=2)
            sb.switch_to_default_content()
            return True
    except Exception:
        try: sb.switch_to_default_content()
        except Exception: pass

    try:
        sb.execute_script("""
            var ts = document.querySelector('.cf-turnstile');
            if (ts) ts.click();
        """)
    except Exception: pass
    return False

def wait_turnstile(sb, timeout: int = 60) -> bool:
    print("🔍 正在检查 Cloudflare Turnstile 验证码...")
    
    # 动态检测是否存在验证码（防抢跑）
    has_cf = False
    for _ in range(3):
        try:
            has_cf = sb.execute_script("""
                return !!document.querySelector('.cf-turnstile') || 
                       !!document.querySelector('iframe[src*="challenges.cloudflare"]') ||
                       !!document.querySelector('input[name="cf-turnstile-response"]');
            """)
            if has_cf: break
        except Exception: pass
        time.sleep(2)
        
    if not has_cf:
        print("ℹ️ 页面上未检测到 Turnstile 验证码框，直接跳过喵。")
        return True

    print("⏳ 发现 Turnstile 验证码，耐心等待并尝试打勾...")
    time.sleep(3)
    
    try:
        sb.execute_script("""
            var ts = document.querySelector('.cf-turnstile') || document.querySelector('iframe[src*="challenges.cloudflare"]');
            if (ts) ts.scrollIntoView({block:'center'});
        """)
    except Exception: pass

    start = time.time()
    last_click = 0

    while time.time() - start < timeout:
        if _turnstile_token_ready(sb):
            print("✅ Turnstile 绿勾验证完成！")
            time.sleep(2) 
            return True

        now = time.time()
        if now - last_click >= 4:
            print("🖱️ 尝试戳一下中间的框框...")
            _try_click_turnstile(sb)
            last_click = now

        time.sleep(1)

    print("❌ Turnstile 打勾超时！")
    return _turnstile_token_ready(sb)

# ============================================================
# 辅助函数
# ============================================================

def read_alert(sb) -> str:
    try:
        alerts = sb.find_elements("div.alert")
        for alert in alerts:
            text = (alert.text or "").strip()
            if text: return text
    except Exception: pass
    return ""

def extract_remaining_minutes(sb):
    try:
        page_text = sb.get_page_source()
        if re.search(r'Expiry\s*\(Next Renewal\).*?Expired', page_text, re.IGNORECASE | re.DOTALL):
            print("⚠️ 检测到服务器已过期（Expired）")
            return 0
        
        span_match = re.search(
            r'Expiry\s*\(Next Renewal\).*?<span[^>]*>((?:\d+d\s*)?(?:\d+h\s*)?(?:\d+m\s*)?)</span>',
            page_text, re.IGNORECASE | re.DOTALL
        )
        
        if span_match:
            time_str = span_match.group(1).strip()
            days = hours = minutes = 0
            d_match = re.search(r'(\d+)d', time_str)
            h_match = re.search(r'(\d+)h', time_str)
            m_match = re.search(r'(\d+)m', time_str)
            if d_match: days = int(d_match.group(1))
            if h_match: hours = int(h_match.group(1))
            if m_match: minutes = int(m_match.group(1))
            total_minutes = days * 24 * 60 + hours * 60 + minutes
            if total_minutes > 0:
                print(f"✅ 成功提取时间: {total_minutes} 分钟")
                return total_minutes
        
        # 修复了 Python 3.12 的 \d 语法警告 (加了 r 前缀)
        js_extract = r"""
        (function() {
            var spans = document.querySelectorAll('span.font-medium.text-foreground, span.text-foreground');
            for (var i = 0; i < spans.length; i++) {
                var text = spans[i].textContent.trim();
                if (/\d+[dhm]/.test(text)) {
                    return text;
                }
            }
            var expiredSpans = document.querySelectorAll('span.text-red-400, span.font-medium.text-red-400');
            for (var i = 0; i < expiredSpans.length; i++) {
                if (expiredSpans[i].textContent.trim().toLowerCase() === 'expired') {
                    return 'Expired';
                }
            }
            return null;
        })();
        """
        try:
            time_text = sb.execute_script(js_extract)
            if time_text:
                if time_text.lower() == 'expired':
                    print("⚠️ 检测到服务器已过期（JavaScript 方法）")
                    return 0
                days = hours = minutes = 0
                d_match = re.search(r'(\d+)d', time_text)
                h_match = re.search(r'(\d+)h', time_text)
                m_match = re.search(r'(\d+)m', time_text)
                if d_match: days = int(d_match.group(1))
                if h_match: hours = int(h_match.group(1))
                if m_match: minutes = int(m_match.group(1))
                total_minutes = days * 24 * 60 + hours * 60 + minutes
                if total_minutes > 0:
                    print(f"✅ 成功提取时间（JavaScript）: {total_minutes} 分钟")
                    return total_minutes
        except Exception as e:
            print(f"⚠️ JavaScript 提取失败: {e}")
            
        print("⚠️ 所有提取方法均未成功")
        return None
    except Exception as e:
        print(f"⚠️ 提取剩余时间异常: {e}")
        return None

# ============================================================
# 登录
# ============================================================

def login(sb) -> bool:
    print("\n" + "#" * 25)
    print("   开始 ZamPTO 登录")
    print("#" * 25)

    login_url = f"{BASE_URL}/auth/login"
    print(f"🌐 打开登录页面: {login_url}")

    try:
        sb.uc_open_with_reconnect(login_url, reconnect_time=8)
    except Exception as exc:
        print(f"⚠️ 打开登录页面失败: {exc}")
        return False

    print("⏳ 等待登录表单加载……")
    try:
        sb.wait_for_element(EMAIL_SELECTOR, timeout=30)
        sb.wait_for_element(PASSWORD_SELECTOR, timeout=30)
        print("✅ 登录表单加载成功")
    except Exception as exc:
        print(f"❌ 登录表单未加载成功: {exc}")
        sb.save_screenshot("login_form_fail.png")
        return False

    try:
        for button in sb.find_elements("button"):
            text = (button.text or "").strip().lower()
            if text in {"accept", "accept all", "同意", "接受"}:
                button.click()
                time.sleep(1)
                break
    except Exception: pass

    print(f"📧 填写邮箱 ({EMAIL_SELECTOR})……")
    sb.update_text(EMAIL_SELECTOR, EMAIL)
    print(f"🔑 填写密码 ({PASSWORD_SELECTOR})……")
    sb.update_text(PASSWORD_SELECTOR, PASSWORD)
    time.sleep(1)

    # 🚨 替换原作者的 Turnstile 逻辑为喵酱打勾逻辑 🚨
    if not wait_turnstile(sb, timeout=60):
        print("❌ 登录界面的 Turnstile 验证未通过，尝试强行提交！")

    print("🖱️ 敲击回车提交表单...")
    sb.press_keys(PASSWORD_SELECTOR, '\n')

    print("⏳ 等待登录结果……")
    login_paths = {"/auth/login", "/login"}
    for i in range(30):
        time.sleep(1)
        current_url = sb.get_current_url()
        normalized = current_url.split("?", 1)[0].rstrip("/").lower()
        if "://" in normalized:
            from urllib.parse import urlparse
            normalized = urlparse(normalized).path.rstrip("/").lower()

        alert_text = read_alert(sb)
        if alert_text:
            lowered = alert_text.lower()
            if any(kw in lowered for kw in ("invalid", "incorrect", "wrong password", "invalid credentials")):
                print("❌ 账号或密码错误")
                sb.save_screenshot("login_failed.png")
                return False

        if normalized not in login_paths:
            print("✅ 登录成功！")
            return True

        if not sb.is_element_present(EMAIL_SELECTOR) and not sb.is_element_present(PASSWORD_SELECTOR):
            print("✅ 登录表单已消失，判定登录成功")
            return True

    print("❌ 登录超时（30秒）")
    sb.save_screenshot("login_timeout.png")
    return False

# ============================================================
# 获取服务器 ID 列表
# ============================================================

def get_server_ids(sb) -> list:
    print("🔍 正在提取服务器 ID 列表...")
    time.sleep(5)
    server_ids = []

    try:
        page_text = sb.get_page_source()
        pattern = r'ID:\s*(\d+)'
        matches = re.findall(pattern, page_text)
        if matches:
            server_ids = list(set(matches))
            print(f"✅ 找到 {len(server_ids)} 个服务器 ID: {server_ids}")
            return server_ids
    except Exception: pass
    return []

# ============================================================
# 续期单个服务器
# ============================================================

def renew_one_server_by_id(sb, server_id, index) -> dict:
    result = {
        "index": index,
        "server_id": server_id,
        "server_name": f"Server-{server_id}",
        "status": "unknown",
        "detail": ""
    }

    try:
        detail_url = f"{BASE_URL}/server?id={server_id}"
        print(f"\n🔄 正在处理第 {index+1} 个服务器: ID={server_id}")
        sb.get(detail_url)

        print("⏳ 等待页面关键内容加载...")
        try:
            sb.wait_for_text("Server last renewed", timeout=15)
        except Exception:
            try: sb.wait_for_text("Expiry (Next Renewal)", timeout=10)
            except Exception: pass
        time.sleep(3)

        current_url = sb.get_current_url()
        if "server" not in current_url.lower():
            result["status"] = "failed"
            result["detail"] = "未进入详情页"
            return result

        old_minutes = extract_remaining_minutes(sb)
        if old_minutes is not None:
            if old_minutes == 0: print(f"📅 原始状态: 已过期（Expired）")
            else: print(f"📅 原始剩余时间: {old_minutes} 分钟")

        click_success = False
        print("🖱️ 尝试点击 Renew Server 按钮...")
        
        # 修复了 Python 3.12 的语法警告
        click_script = r"""
        (function() {
            var xpath = "//div[@data-slot='card'][.//div[contains(text(),'Server last renewed')]]//button[normalize-space()='Renew Server']";
            var button = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (button) {
                button.scrollIntoView({behavior: 'smooth', block: 'center'});
                button.click();
                return 'success';
            }
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].textContent.trim() === 'Renew Server') {
                    buttons[i].scrollIntoView({behavior: 'smooth', block: 'center'});
                    buttons[i].click();
                    return 'success';
                }
            }
            return 'not_found';
        })();
        """
        try:
            if sb.execute_script(click_script) == 'success':
                print("✅ 点击成功")
                click_success = True
        except Exception as e: print(f"⚠️ 点击失败: {e}")

        if not click_success:
            result["status"] = "error"
            result["detail"] = "无法点击续期按钮"
            return result

        print("⏳ 等待 5 秒后检查时间变化...")
        time.sleep(5)
        early_success = False
        quick_check_minutes = extract_remaining_minutes(sb)
        
        if old_minutes is not None and quick_check_minutes is not None:
            time_change = quick_check_minutes - old_minutes
            if time_change > 1000:
                result["status"] = "success"
                result["detail"] = f"✅ 续期成功！"
                print("✅ 提前确认续期成功，跳过 Turnstile 处理")
                early_success = True
            elif old_minutes == 0 and quick_check_minutes > 0:
                result["status"] = "success"
                result["detail"] = f"✅ 从过期恢复！"
                print("✅ 提前确认从过期恢复，跳过 Turnstile 处理")
                early_success = True

        if not early_success:
            # 🚨 在详情页面如果弹出 Turnstile，调用喵酱的打勾模块 🚨
            wait_turnstile(sb, timeout=60)

            print("⏳ 重新加载详情页获取最终状态...")
            time.sleep(3)
            try:
                sb.get(detail_url)
                sb.wait_for_text("Server last renewed", timeout=15)
                time.sleep(3)
            except Exception: pass

            new_minutes = extract_remaining_minutes(sb)
            alert_text = read_alert(sb)

            if old_minutes is not None and new_minutes is not None:
                time_change = new_minutes - old_minutes
                if time_change > 1000:
                    result["status"] = "success"
                    result["detail"] = f"✅ 续期成功！"
                elif old_minutes == 0 and new_minutes > 0:
                    result["status"] = "success"
                    result["detail"] = f"✅ 从过期恢复！"
                elif -10 < time_change < 100:
                    result["status"] = "skipped"
                    result["detail"] = f"⏭️ 可能已续期"
                else:
                    result["status"] = "unknown"
                    result["detail"] = f"时间变化异常"
            elif alert_text and any(kw in alert_text.lower() for kw in ("renewed", "success", "extended")):
                result["status"] = "success"
                result["detail"] = alert_text
            else:
                result["status"] = "unknown"
                result["detail"] = "无法确认续期结果"
        
        return result
    except Exception as e:
        result["status"] = "error"
        result["detail"] = str(e)
        return result

# ============================================================
# 主程序
# ============================================================

def renew_all_servers_by_id(sb) -> list:
    print("\n" + "#" * 25)
    print("   开始 ZamPTO 自动续期流程")
    print("#" * 25)

    server_ids = get_server_ids(sb)
    if not server_ids:
        print("❌ 未获取到任何服务器 ID")
        return []

    results = []
    for idx, server_id in enumerate(server_ids):
        res = renew_one_server_by_id(sb, server_id, idx)
        results.append(res)
        print(f"📊 续期结果: {res['status']} - {res['detail']}")

    success = sum(1 for r in results if r['status'] == 'success')
    skipped = sum(1 for r in results if r['status'] == 'skipped')
    failed = sum(1 for r in results if r['status'] in ('failed', 'error', 'unknown'))

    summary = (
        f"续期完成：共 {len(results)} 个服务器\n"
        f"✅ 成功: {success}\n"
        f"⏭️ 跳过: {skipped}\n"
        f"❌ 失败: {failed}"
    )
    detail = "\n".join([f"  #{r['index']+1} ID={r['server_id']}: {r['status']}" for r in results])
    
    send_tg_message("📋", summary, detail)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)
    return results

def main():
    print("#" * 25)
    print("   ZamPTO 自动登录续期 (喵酱打勾版)")
    print("#" * 25)

    if not EMAIL or not PASSWORD:
        print("❌ 未配置账号")
        raise SystemExit(1)

    sb_kwargs = {"uc": True, "headless": False}
    if IS_PROXY:
        sb_kwargs["proxy"] = PROXY_SERVER

    try:
        with SB(**sb_kwargs) as sb:
            try:
                sb.open("https://api.ip.sb/ip")
                print(f"📍 当前出口 IP: {sb.get_text('body').strip()}")
            except Exception: pass

            if login(sb):
                print("\n🎉 登录流程成功")
                renew_all_servers_by_id(sb)
            else:
                print("\n❌ 登录失败，终止续期操作。")
                send_tg_message("❌", "登录失败")
                raise SystemExit(1)
    except Exception as exc:
        print(f"❌ 程序运行异常: {exc}")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
