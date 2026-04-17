"""Thin async client for kie.ai image generation.

Used by the dashboard to create cover images from text prompts.
Uses Flux 2 Pro text-to-image (16:9 by default for Telegram previews).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Tuple

import httpx

logger = logging.getLogger(__name__)

KIE_BASE = "https://api.kie.ai/api/v1"

_EXT_BY_CTYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


async def _create_task(client: httpx.AsyncClient, api_key: str, prompt: str,
                        aspect_ratio: str, resolution: str) -> str:
    r = await client.post(
        f"{KIE_BASE}/jobs/createTask",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "flux-2/pro-text-to-image",
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            },
        },
        timeout=30.0,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("code") != 200:
        raise RuntimeError(f"kie.ai createTask failed: {payload}")
    task_id = payload["data"]["taskId"]
    logger.info("kie.ai task created: %s", task_id)
    return task_id


async def _poll_task(client: httpx.AsyncClient, api_key: str, task_id: str,
                      timeout_s: float = 180.0, interval_s: float = 2.0) -> str:
    """Poll until state == success; return the image URL."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(
            f"{KIE_BASE}/jobs/recordInfo",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"taskId": task_id},
            timeout=15.0,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data") or {}
        state = data.get("state")
        if state == "success":
            result = json.loads(data.get("resultJson", "{}"))
            urls = result.get("resultUrls") or []
            if not urls:
                raise RuntimeError(f"kie.ai success but no resultUrls: {data}")
            return urls[0]
        if state == "fail":
            raise RuntimeError(f"kie.ai task failed: {data.get('failMsg')}")
        await asyncio.sleep(interval_s)
    raise TimeoutError(f"kie.ai task {task_id} did not complete within {timeout_s}s")


async def generate_image_sync(*, api_key: str, prompt: str,
                               aspect_ratio: str = "16:9",
                               resolution: str = "1K") -> Tuple[bytes, str]:
    """Create a task, wait for it, and download the resulting image.

    Returns (image_bytes, extension).
    """
    async with httpx.AsyncClient() as client:
        task_id = await _create_task(client, api_key, prompt, aspect_ratio, resolution)
        image_url = await _poll_task(client, api_key, task_id)
        ir = await client.get(image_url, timeout=60.0)
        ir.raise_for_status()
        ctype = (ir.headers.get("content-type", "").split(";")[0]).lower()
        ext = _EXT_BY_CTYPE.get(ctype, "png")
        return ir.content, ext
