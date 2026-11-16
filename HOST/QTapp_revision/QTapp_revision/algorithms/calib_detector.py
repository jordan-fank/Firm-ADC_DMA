# -*- coding: utf-8 -*-
"""Tape-ROI inner-hole center detector.

This module keeps the old public API used by CalibController:
`DetectorParams` and `run_pipeline(frame, params)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


Rect = Tuple[int, int, int, int]


@dataclass
class DetectorParams:
    blur_ksize: int = 5
    canny_lo: int = 50
    canny_hi: int = 150
    min_area: int = 60
    roi: Optional[Rect] = None
    tape_hsv: Optional[Tuple[int, int, int]] = (102, 183, 249)
    hsv_h_tolerance: int = 10
    hsv_s_tolerance: int = 55
    hsv_v_tolerance: int = 55
    roi_inner_margin: int = 18
    min_marker_area: int = 40
    radial_step_degrees: float = 1.0
    radial_min_points: int = 80
    ellipse_ransac_iterations: int = 160
    ransac_inlier_tolerance: float = 3.0
    ellipse_cluster_center_tolerance: float = 8.0
    ellipse_cluster_axis_tolerance: float = 12.0
    ellipse_cluster_angle_tolerance: float = 18.0
    ellipse_min_cluster_size: int = 4
    min_axis: float = 10.0
    max_axis: float = 1200.0
    max_axis_ratio: float = 4.0
    max_mean_error: float = 4.0
    subpixel_enabled: bool = True
    subpixel_search_radius: float = 2.0
    subpixel_step: float = 0.5
    subpixel_min_gradient: float = 3.0
    subpixel_max_shift: float = 2.0
    final_refit_min_points: int = 30
    ignore_mask: Optional[np.ndarray] = None


def _clamp_rect(rect: Rect, width: int, height: int) -> Optional[Rect]:
    x, y, w, h = rect
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(width, int(x + w))
    y1 = min(height, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _shrink_rect(rect: Rect, margin: int) -> Optional[Rect]:
    x, y, w, h = rect
    nw = w - margin * 2
    nh = h - margin * 2
    if nw <= 0 or nh <= 0:
        return None
    return x + margin, y + margin, nw, nh


def _hsv_mask_from_sample(frame: np.ndarray, hsv: Tuple[int, int, int], params: DetectorParams) -> np.ndarray:
    hsv_img = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv
    lo_s = max(0, s - params.hsv_s_tolerance)
    hi_s = min(255, s + params.hsv_s_tolerance)
    lo_v = max(0, v - params.hsv_v_tolerance)
    hi_v = min(255, v + params.hsv_v_tolerance)
    dh = params.hsv_h_tolerance
    if h - dh < 0:
        mask1 = cv2.inRange(hsv_img, (0, lo_s, lo_v), (h + dh, hi_s, hi_v))
        mask2 = cv2.inRange(hsv_img, (180 + h - dh, lo_s, lo_v), (179, hi_s, hi_v))
        return cv2.bitwise_or(mask1, mask2)
    if h + dh > 179:
        mask1 = cv2.inRange(hsv_img, (h - dh, lo_s, lo_v), (179, hi_s, hi_v))
        mask2 = cv2.inRange(hsv_img, (0, lo_s, lo_v), (h + dh - 180, hi_s, hi_v))
        return cv2.bitwise_or(mask1, mask2)
    return cv2.inRange(hsv_img, (h - dh, lo_s, lo_v), (h + dh, hi_s, hi_v))


def sample_hsv_color(frame: np.ndarray, x: int, y: int, radius: int = 4) -> Tuple[int, int, int]:
    h, w = frame.shape[:2]
    x0 = max(0, x - radius)
    y0 = max(0, y - radius)
    x1 = min(w, x + radius + 1)
    y1 = min(h, y + radius + 1)
    patch = frame[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mean = hsv.reshape(-1, 3).mean(axis=0)
    return int(mean[0]), int(mean[1]), int(mean[2])


def _find_roi_from_color_markers(mask: np.ndarray, params: DetectorParams) -> tuple[Optional[Rect], np.ndarray]:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=1)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    rects: List[Rect] = []
    vis = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    for label_id in range(1, num_labels):
        area = int(stats[label_id, cv2.CC_STAT_AREA])
        if area < params.min_marker_area:
            continue
        x = int(stats[label_id, cv2.CC_STAT_LEFT])
        y = int(stats[label_id, cv2.CC_STAT_TOP])
        w = int(stats[label_id, cv2.CC_STAT_WIDTH])
        h = int(stats[label_id, cv2.CC_STAT_HEIGHT])
        rects.append((x, y, w, h))
        color = ((label_id * 73) % 180 + 60, (label_id * 47) % 180 + 60, (label_id * 29) % 180 + 60)
        vis[labels == label_id] = color
    if len(rects) < 4:
        return None, vis
    left = min(x for x, _, _, _ in rects)
    top = min(y for _, y, _, _ in rects)
    right = max(x + w for x, _, w, _ in rects)
    bottom = max(y + h for _, y, _, h in rects)
    roi = _shrink_rect((left, top, right - left, bottom - top), params.roi_inner_margin)
    if roi is None:
        return None, vis
    return _clamp_rect(roi, mask.shape[1], mask.shape[0]), vis


def _find_roi_from_tape_region(mask: np.ndarray, params: DetectorParams) -> tuple[Optional[Rect], np.ndarray]:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 13))
    connected = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    connected = cv2.dilate(connected, kernel, iterations=1)
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    vis = cv2.cvtColor(connected, cv2.COLOR_GRAY2BGR)
    if not contours:
        return None, vis
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < params.min_marker_area * 4:
        return None, vis
    x, y, w, h = cv2.boundingRect(contour)
    cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 255), 2)
    roi = _shrink_rect((x, y, w, h), params.roi_inner_margin)
    if roi is None:
        return None, vis
    return _clamp_rect(roi, mask.shape[1], mask.shape[0]), vis


def _auto_or_manual_roi(frame: np.ndarray, params: DetectorParams) -> tuple[Optional[Rect], Dict[str, np.ndarray], str]:
    steps: Dict[str, np.ndarray] = {}
    if params.roi is not None:
        h, w = frame.shape[:2]
        roi = _clamp_rect(params.roi, w, h)
        if roi is not None:
            return roi, steps, "manual"
    if params.tape_hsv is not None:
        mask = _hsv_mask_from_sample(frame, params.tape_hsv, params)
        roi, marker_vis = _find_roi_from_color_markers(mask, params)
        steps["胶带颜色Mask"] = mask
        steps["颜色标记连通块"] = marker_vis
        if roi is not None:
            return roi, steps, "tape_markers"
        roi, region_vis = _find_roi_from_tape_region(mask, params)
        steps["胶带范围连通"] = region_vis
        if roi is not None:
            return roi, steps, "tape_region"
    return None, steps, "none"


def locate_roi(frame: np.ndarray, params: Optional[DetectorParams] = None) -> tuple[Optional[Rect], str]:
    """Locate the active detection ROI without running the full circle detector."""
    if params is None:
        params = DetectorParams()
    roi, _, source = _auto_or_manual_roi(frame, params)
    return roi, source


def _clean_small_edges(edges: np.ndarray, params: DetectorParams) -> tuple[np.ndarray, Optional[Rect]]:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    clean = np.zeros_like(edges)
    for label_id in range(1, num_labels):
        area = int(stats[label_id, cv2.CC_STAT_AREA])
        if area >= params.min_area:
            clean[labels == label_id] = 255
    ys, xs = np.where(clean > 0)
    if len(xs) < params.min_area:
        return clean, None
    x0 = int(np.percentile(xs, 5.0))
    x1 = int(np.percentile(xs, 95.0))
    y0 = int(np.percentile(ys, 5.0))
    square = max(x1 - x0 + 1, int(params.min_axis))
    cx = (x0 + x1) / 2.0
    bx = int(max(0, min(edges.shape[1] - square, round(cx - square / 2.0))))
    by = int(max(0, min(edges.shape[0] - square, y0)))
    return clean, (bx, by, int(min(square, edges.shape[1] - bx)), int(min(square, edges.shape[0] - by)))


def _apply_ignore_mask_to_edges(edges: np.ndarray, ignore_mask: Optional[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    if ignore_mask is None:
        return edges.copy(), np.zeros_like(edges)
    mask = ignore_mask[:, :, 0] if ignore_mask.ndim == 3 else ignore_mask
    if mask.shape[:2] != edges.shape[:2]:
        return edges.copy(), np.zeros_like(edges)
    mask_bin = (mask > 0).astype(np.uint8) * 255
    filtered = edges.copy()
    filtered[mask_bin > 0] = 0
    return filtered, mask_bin


def _radial_points(edges: np.ndarray, params: DetectorParams, seed: tuple[float, float]) -> np.ndarray:
    h, w = edges.shape[:2]
    cx, cy = seed
    max_radius = int(max(3.0, np.hypot(w, h)))
    min_radius = int(max(3.0, params.min_axis / 2.0))
    points: List[tuple[float, float]] = []
    for angle_deg in np.arange(0.0, 360.0, max(float(params.radial_step_degrees), 0.5)):
        theta = np.deg2rad(angle_deg)
        dx = cos(theta)
        dy = sin(theta)
        last_xy = None
        for radius in range(min_radius, max_radius + 1):
            x = int(round(cx + dx * radius))
            y = int(round(cy + dy * radius))
            if last_xy == (x, y):
                continue
            last_xy = (x, y)
            if x <= 0 or y <= 0 or x >= w - 1 or y >= h - 1:
                break
            if np.any(edges[y - 1:y + 2, x - 1:x + 2] > 0):
                points.append((float(x), float(y)))
                break
    return np.array(points, dtype=np.float32)


def _sample_gray(gray: np.ndarray, x: float, y: float) -> Optional[float]:
    h, w = gray.shape[:2]
    if x < 0 or y < 0 or x >= w - 1 or y >= h - 1:
        return None
    x0 = int(np.floor(x))
    y0 = int(np.floor(y))
    dx = x - x0
    dy = y - y0
    return float(
        gray[y0, x0] * (1 - dx) * (1 - dy)
        + gray[y0, x0 + 1] * dx * (1 - dy)
        + gray[y0 + 1, x0] * (1 - dx) * dy
        + gray[y0 + 1, x0 + 1] * dx * dy
    )


def _refine_subpixel(gray: np.ndarray, points: np.ndarray, seed: tuple[float, float], params: DetectorParams) -> np.ndarray:
    if len(points) == 0 or not params.subpixel_enabled:
        return points.copy()
    gray_f = gray.astype(np.float32)
    sx, sy = seed
    offsets = np.arange(
        -params.subpixel_search_radius,
        params.subpixel_search_radius + params.subpixel_step * 0.5,
        max(params.subpixel_step, 0.1),
        dtype=np.float32,
    )
    refined: List[tuple[float, float]] = []
    for px, py in points:
        vx = float(px) - sx
        vy = float(py) - sy
        norm = float(np.hypot(vx, vy))
        if norm < 1e-6:
            refined.append((float(px), float(py)))
            continue
        dx = vx / norm
        dy = vy / norm
        values = []
        valid = True
        for off in offsets:
            sample = _sample_gray(gray_f, float(px) + dx * float(off), float(py) + dy * float(off))
            if sample is None:
                valid = False
                break
            values.append(sample)
        if not valid or len(values) < 3:
            refined.append((float(px), float(py)))
            continue
        gradients = np.abs(np.diff(np.array(values, dtype=np.float32)))
        best = int(np.argmax(gradients))
        if float(gradients[best]) < params.subpixel_min_gradient:
            refined.append((float(px), float(py)))
            continue
        correction = float((offsets[best] + offsets[best + 1]) / 2.0)
        if abs(correction) > params.subpixel_max_shift:
            refined.append((float(px), float(py)))
            continue
        refined.append((float(px) + dx * correction, float(py) + dy * correction))
    return np.array(refined, dtype=np.float32)


def _canonical_ellipse(ellipse: tuple[Any, Any, float]) -> tuple[float, float, float, float, float]:
    (cx, cy), (a1, a2), angle = ellipse
    a1 = float(a1)
    a2 = float(a2)
    angle = float(angle) % 180.0
    if a2 > a1:
        a1, a2 = a2, a1
        angle = (angle + 90.0) % 180.0
    return float(cx), float(cy), a1, a2, angle


def _ellipse_from_params(params: tuple[float, float, float, float, float]) -> tuple[Any, Any, float]:
    cx, cy, long_axis, short_axis, angle = params
    return (cx, cy), (long_axis, short_axis), angle


def _ellipse_distances(points: np.ndarray, ellipse: tuple[Any, Any, float]) -> np.ndarray:
    (cx, cy), (axis1, axis2), angle = ellipse
    a = axis1 / 2.0
    b = axis2 / 2.0
    if a <= 0 or b <= 0:
        return np.full((len(points),), 9999.0, dtype=np.float32)
    theta = np.deg2rad(angle)
    cos_t = np.cos(theta)
    sin_t = np.sin(theta)
    dx = points[:, 0] - cx
    dy = points[:, 1] - cy
    xr = cos_t * dx + sin_t * dy
    yr = -sin_t * dx + cos_t * dy
    normalized = np.sqrt((xr / a) ** 2 + (yr / b) ** 2)
    return np.abs(normalized - 1.0) * ((a + b) / 2.0)


def _ellipse_error_coverage(points: np.ndarray, ellipse: tuple[Any, Any, float]) -> tuple[float, float]:
    distances = _ellipse_distances(points, ellipse)
    mean_error = float(np.mean(distances)) if len(distances) else 9999.0
    (cx, cy), (axis1, axis2), angle = ellipse
    theta = np.deg2rad(angle)
    dx = points[:, 0] - cx
    dy = points[:, 1] - cy
    xr = np.cos(theta) * dx + np.sin(theta) * dy
    yr = -np.sin(theta) * dx + np.cos(theta) * dy
    bins = np.floor((np.arctan2(yr / max(axis2 / 2.0, 1.0), xr / max(axis1 / 2.0, 1.0)) + pi) / (2 * pi) * 36)
    coverage = float(len(set(int(np.clip(v, 0, 35)) for v in bins)) / 36.0) if len(points) else 0.0
    return mean_error, coverage


def _fit_by_votes(points: np.ndarray, params: DetectorParams) -> Optional[tuple[Any, float, float, np.ndarray]]:
    if len(points) < max(5, params.min_area):
        return None
    rng = np.random.default_rng(20260604)
    candidates: List[dict[str, Any]] = []
    for _ in range(min(params.ellipse_ransac_iterations, max(1, len(points) * 2))):
        sample = points[rng.choice(len(points), size=5, replace=False)].reshape(-1, 1, 2).astype(np.float32)
        try:
            ellipse = cv2.fitEllipse(sample)
        except cv2.error:
            continue
        cx, cy, long_axis, short_axis, angle = _canonical_ellipse(ellipse)
        if short_axis < params.min_axis or long_axis > params.max_axis:
            continue
        if long_axis / max(short_axis, 1.0) > params.max_axis_ratio:
            continue
        ellipse = _ellipse_from_params((cx, cy, long_axis, short_axis, angle))
        dist = _ellipse_distances(points, ellipse)
        inliers = int(np.count_nonzero(dist <= params.ransac_inlier_tolerance))
        mean_error, coverage = _ellipse_error_coverage(points, ellipse)
        candidates.append({"ellipse": ellipse, "params": (cx, cy, long_axis, short_axis, angle), "inliers": inliers, "mean_error": mean_error, "coverage": coverage})
    if not candidates:
        return None
    clusters: List[List[dict[str, Any]]] = []
    for cand in sorted(candidates, key=lambda x: x["inliers"], reverse=True):
        placed = False
        for cluster in clusters:
            ref = np.median(np.array([c["params"][:4] for c in cluster], dtype=np.float32), axis=0)
            center_dist = float(np.hypot(cand["params"][0] - ref[0], cand["params"][1] - ref[1]))
            if center_dist <= params.ellipse_cluster_center_tolerance and abs(cand["params"][2] - ref[2]) <= params.ellipse_cluster_axis_tolerance and abs(cand["params"][3] - ref[3]) <= params.ellipse_cluster_axis_tolerance:
                cluster.append(cand)
                placed = True
                break
        if not placed:
            clusters.append([cand])
    clusters = [c for c in clusters if len(c) >= params.ellipse_min_cluster_size]
    if not clusters:
        return None
    best_cluster = max(clusters, key=lambda c: (len(c), np.mean([x["inliers"] for x in c]), -np.mean([x["mean_error"] for x in c])))
    p = np.array([c["params"] for c in best_cluster], dtype=np.float32)
    ellipse = _ellipse_from_params(tuple(float(v) for v in np.median(p, axis=0)))
    dist = _ellipse_distances(points, ellipse)
    mask = dist <= params.ransac_inlier_tolerance
    if int(np.count_nonzero(mask)) >= max(5, params.final_refit_min_points):
        try:
            ellipse = cv2.fitEllipse(points[mask].reshape(-1, 1, 2).astype(np.float32))
        except cv2.error:
            pass
        ellipse = _ellipse_from_params(_canonical_ellipse(ellipse))
        dist = _ellipse_distances(points, ellipse)
        mask = dist <= params.ransac_inlier_tolerance
    mean_error, coverage = _ellipse_error_coverage(points[mask] if np.any(mask) else points, ellipse)
    if mean_error > params.max_mean_error:
        return None
    return ellipse, mean_error, coverage, mask


def _draw_points_overlay(roi_img: np.ndarray, points: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    vis = roi_img.copy()
    for px, py in points:
        x = int(round(float(px)))
        y = int(round(float(py)))
        if 0 <= x < vis.shape[1] and 0 <= y < vis.shape[0]:
            cv2.circle(vis, (x, y), 1, color, -1, lineType=cv2.LINE_AA)
    return vis


def _draw_roi_overlay(frame: np.ndarray, roi: Optional[Rect]) -> np.ndarray:
    if roi is None:
        return frame.copy()
    result = frame.copy()
    x, y, w, h = roi
    cv2.rectangle(result, (x, y), (x + w, y + h), (165, 185, 185), 1, lineType=cv2.LINE_AA)
    return result


def run_pipeline(frame: np.ndarray, params: Optional[DetectorParams] = None) -> Tuple[np.ndarray, Dict[str, np.ndarray], List[Dict[str, Any]]]:
    if params is None:
        params = DetectorParams()

    roi, steps, roi_source = _auto_or_manual_roi(frame, params)
    overlay = _draw_roi_overlay(frame, roi)
    contour_info: List[Dict[str, Any]] = []
    if roi is None:
        return overlay, steps, contour_info

    x, y, w, h = roi
    roi_img = frame[y:y + h, x:x + w].copy()
    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    k = params.blur_ksize if params.blur_ksize % 2 == 1 else params.blur_ksize + 1
    blurred = cv2.GaussianBlur(gray, (k, k), 0)
    edges = cv2.Canny(blurred, params.canny_lo, params.canny_hi)
    roi_ignore_mask = None
    if params.ignore_mask is not None and params.ignore_mask.shape[:2] == frame.shape[:2]:
        roi_ignore_mask = params.ignore_mask[y:y + h, x:x + w]
    masked_edges, roi_ignore_mask_vis = _apply_ignore_mask_to_edges(edges, roi_ignore_mask)
    clean_edges, seed_bbox = _clean_small_edges(masked_edges, params)
    if seed_bbox is None:
        seed = ((w - 1) / 2.0, (h - 1) / 2.0)
    else:
        bx, by, bw, bh = seed_bbox
        seed = (float(bx + bw / 2.0), float(by + bh / 2.0))
    radial = _radial_points(clean_edges, params, seed)
    subpixel = _refine_subpixel(gray, radial, seed, params)
    subpixel_shift = (
        np.hypot(subpixel[:, 0] - radial[:, 0], subpixel[:, 1] - radial[:, 1])
        if len(subpixel) == len(radial) and len(radial)
        else np.array([], dtype=np.float32)
    )
    fit = _fit_by_votes(subpixel, params) if len(subpixel) >= params.radial_min_points else None

    seed_vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if seed_bbox is not None:
        bx, by, bw, bh = seed_bbox
        cv2.rectangle(seed_vis, (bx, by), (bx + bw, by + bh), (255, 255, 0), 1)
    cv2.drawMarker(seed_vis, (int(round(seed[0])), int(round(seed[1]))), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)

    ransac_vis = np.zeros((h, w, 3), dtype=np.uint8)
    if fit is not None:
        ellipse, mean_error, coverage, mask = fit
        (cx, cy), (axis1, axis2), angle = ellipse
        gx = float(cx + x)
        gy = float(cy + y)
        axes_draw = (int(round(axis1 / 2.0)), int(round(axis2 / 2.0)))
        cv2.ellipse(overlay, (int(round(gx)), int(round(gy))), axes_draw, float(angle), 0, 360, (0, 0, 255), 2)
        cv2.drawMarker(overlay, (int(round(gx)), int(round(gy))), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
        for point, ok in zip(np.round(subpixel).astype(np.int32), mask):
            if 0 <= point[0] < w and 0 <= point[1] < h:
                ransac_vis[point[1], point[0]] = (0, 255, 0) if ok else (0, 0, 255)
        contour_info.append({
            "id": 1,
            "area": float(np.pi * (axis1 / 2.0) * (axis2 / 2.0)),
            "perimeter": float(np.pi * (axis1 + axis2)),
            "cx": int(round(gx)),
            "cy": int(round(gy)),
            "x": int(round(gx - axis1 / 2.0)),
            "y": int(round(gy - axis2 / 2.0)),
            "w": int(round(axis1)),
            "h": int(round(axis2)),
            "mean_error": float(mean_error),
            "coverage": float(coverage),
            "roi_source": roi_source,
            "seed_x": float(seed[0] + x),
            "seed_y": float(seed[1] + y),
            "radial_points": int(len(radial)),
            "subpixel_points": int(len(subpixel)),
            "inlier_points": int(np.count_nonzero(mask)),
            "subpixel_shift_mean": float(np.mean(subpixel_shift)) if len(subpixel_shift) else 0.0,
            "subpixel_shift_max": float(np.max(subpixel_shift)) if len(subpixel_shift) else 0.0,
            "final_refit": int(np.count_nonzero(mask)) >= max(5, params.final_refit_min_points),
        })

    steps.update({
        "ROI原图": roi_img,
        "灰度图": gray,
        "高斯模糊": blurred,
        "Canny边缘": edges,
        "屏蔽Mask(ROI)": roi_ignore_mask_vis,
        "屏蔽后Canny": masked_edges,
        "清理后Canny": clean_edges,
        "自动内部点": seed_vis,
        "粗径向点叠加": _draw_points_overlay(roi_img, radial, (255, 255, 0)),
        "亚像素点叠加": _draw_points_overlay(roi_img, subpixel, (0, 255, 255)),
        "RANSAC投票内点": ransac_vis,
        "最终拟合结果": overlay.copy(),
    })
    return overlay, steps, contour_info
