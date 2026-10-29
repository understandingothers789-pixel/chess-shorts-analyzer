from __future__ import annotations

import io
import math
import re
from urllib.parse import parse_qs, urlparse

from PIL import Image

from ..control import CaptureControl, Cancelled


class AutomationUnavailable(RuntimeError):
    pass


def normalize_url(value: str):
    value = value.strip()
    if not value.startswith(('https://', 'http://')):
        value = 'https://' + value
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('Enter a YouTube video or Shorts URL.')
    host = (parsed.hostname or '').lower()
    parts = parsed.path.strip('/').split('/')
    if host == 'youtu.be' and len(parts) == 1:
        video_id = parts[0]
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [''])[0]
        elif len(parts) == 2 and parts[0] in ('shorts', 'embed', 'live'):
            video_id = parts[1]
        else:
            raise ValueError('The URL must identify a single YouTube video.')
    else:
        raise ValueError('Only youtube.com and youtu.be video links are supported.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('The YouTube video ID must contain 11 valid characters.')
    return 'https://www.youtube.com/watch?v=' + video_id


def capture_page(page, session, control, start=0.0, end=None, progress=None):
    """Seek a regular HTML video. Never click through consent, ads, login, or DRM."""
    video = page.locator('video').first
    try:
        video.wait_for(state='visible', timeout=20_000)
        page.wait_for_function("""() => {
          const v = document.querySelector('video');
          return v && v.readyState >= 2 && Number.isFinite(v.duration) && v.duration > 0;
        }""", timeout=20_000)
        info = video.evaluate("v => ({duration: v.duration, width: v.videoWidth, height: v.videoHeight})")
        if not math.isfinite(start) or start < 0:
            raise ValueError('Start time must be finite and nonnegative.')
        if end is not None and not math.isfinite(end):
            raise ValueError('End time must be finite.')
        end = min(end if end is not None else info['duration'], info['duration'])
        if end <= start:
            raise ValueError('End must be later than start and inside the video.')
        session.data.update({'video_title': page.title(), 'source_duration_seconds': info['duration'],
                             'range_start_seconds': start, 'range_end_seconds': end,
                             'browser_video_dimensions': [info['width'], info['height']],
                             'timestamp_precision': 'HTML video currentTime after seek; compositor screenshot, not guaranteed frame accurate',
                             'capture_backend': 'Playwright element screenshot'})
        session.save()
        interval = session.data['requested_interval_seconds']
        for n in range(math.ceil((end - start) / interval)):
            control.checkpoint()
            target = start + n * interval
            if target >= end - 1e-9:
                break
            if page.locator('.ad-showing').count() or page.locator('.ytp-error').first.is_visible():
                raise AutomationUnavailable('An advertisement or player error prevents reliable capture.')
            actual = video.evaluate("""async (v, target) => {
              v.pause();
              if (v.mediaKeys) throw new Error('Protected playback is not supported.');
              if (Math.abs(v.currentTime - target) > 0.0001) {
                await new Promise((resolve, reject) => {
                  const timer = setTimeout(() => {cleanup(); reject(new Error('Video seek timed out'));}, 8000);
                  const cleanup = () => { clearTimeout(timer); v.removeEventListener('seeked', ready); };
                  const ready = () => { cleanup(); resolve(); };
                  v.addEventListener('seeked', ready, {once: true});
                  v.currentTime = target;
                });
              }
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              if (v.readyState < 2 || Math.abs(v.currentTime - target) > 0.08)
                throw new Error(`Video seeking is not reliable (target ${target}, actual ${v.currentTime}, ready ${v.readyState})`);
              return v.currentTime;
            }""", target)
            control.checkpoint()
            pixels = video.screenshot(type='png', timeout=10_000, animations='disabled')
            with Image.open(io.BytesIO(pixels)) as image:
                session.record(image, media=actual, requested=target)
            if progress:
                progress(n + 1, math.ceil((end - start) / interval))
    except Cancelled:
        session.finish('stopped')
        return session
    except Exception as exc:
        session.finish('error', exc)
        if isinstance(exc, ValueError):
            raise
        raise AutomationUnavailable(f'Automatic capture could not continue: {exc}\nUse Browser window capture for this video.') from exc
    session.finish('complete')
    return session


def capture_youtube(url, session, control: CaptureControl, start=0, end=None, progress=None):
    url = normalize_url(url)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            # Separate temporary profile; no reading of the user's browser credentials.
            browser = playwright.chromium.launch(headless=False)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900}, device_scale_factor=1)
                page.goto(url, wait_until='domcontentloaded', timeout=30_000)
                return capture_page(page, session, control, start, end, progress)
            finally:
                browser.close()
    except (Cancelled, AutomationUnavailable):
        raise
    except Exception as exc:
        session.finish('error', exc)
        raise AutomationUnavailable('Could not open an automated Chromium browser. Run "python -m playwright install chromium". '
                                    'For unavailable, restricted, or sign-in-only videos, use Browser window capture.\n' + str(exc)) from exc
