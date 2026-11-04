# Changes: port render_depth / _render_depth / crop_airway from BronchoNavigation (#56).
"""Local metric depth matching AeroPath's circular equidistant ray geometry.

Slicer's bundled SciPy performs trilinear sampling. No model, CT export or
OpenGL context is needed, so this also works on Macs and headless Slicer.
Encoding stays in the inference server's frozen BREA implementation.
"""

import numpy as np
from scipy import ndimage


def render_depth(
    mask,
    affine,
    origin_world,
    forward,
    up,
    *,
    size=256,
    fov=120,
    far=100,
    step=0.25,
):
    """First 0.5-occupancy exit in radial mm, refined with eight bisections.

    Rays leaving CT bounds or remaining in air at the far plane are invalid.
    No rank encoding, synthesized walls, depth dither or contrast remap.
    """
    return _render_depth(
        np.pad(np.asarray(mask, dtype=np.float32), 1),
        mask.shape,
        affine,
        origin_world,
        forward,
        up,
        size=size,
        fov=fov,
        far=far,
        step=step,
    )


def _render_depth(
    occupancy,
    shape,
    affine,
    origin_world,
    forward,
    up,
    *,
    size=256,
    fov=120,
    far=100,
    step=0.25,
    clearance=None,
):
    inverse = np.linalg.inv(affine)
    origin = inverse[:3, :3] @ origin_world + inverse[:3, 3]

    def sample(points):
        # A zero halo makes constant-mode interpolation exactly equivalent to
        # grid-constant on the original mask, including partial boundary voxels.
        # Constant mode avoids SciPy's costly per-neighbour boundary handling.
        return ndimage.map_coordinates(
            occupancy, (points + 1).T, order=1, mode="constant", cval=0, prefilter=False
        )

    if sample(origin[None])[0] <= 0.5:
        raise ValueError("A marker lies outside the airway lumen")
    axis = (np.arange(size) + 0.5) / size * 2 - 1
    yy, xx = np.meshgrid(-axis, axis, indexing="ij")
    radius = np.hypot(xx, yy)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    theta = radius * np.deg2rad(fov) / 2
    radial = (xx[..., None] * right + yy[..., None] * up) / np.maximum(
        radius[..., None], 1e-12
    )
    directions = np.cos(theta)[..., None] * forward + np.sin(theta)[..., None] * radial
    directions /= np.linalg.norm(directions, axis=-1, keepdims=True)
    # A three-coordinate transform does not benefit from BLAS thread dispatch.
    rays = np.einsum(
        "ni,ji->nj", directions.reshape(-1, 3), inverse[:3, :3], optimize=False
    )
    active = (radius <= 1).ravel()
    depth = np.zeros(size * size, np.float32)
    valid = np.zeros(size * size, bool)
    lower = np.zeros(size * size, dtype=float)
    upper = np.zeros(size * size, dtype=float)
    crossed = np.zeros(size * size, bool)
    previous = 0.0
    distances = np.r_[np.arange(step, far, step), far]
    if clearance is not None:
        spacing = np.linalg.norm(affine[:3, :3], axis=0)
        axes = affine[:3, :3] / spacing
        # EDT distances assume orthogonal voxel axes. Fall back for sheared grids.
        if np.allclose(axes.T @ axes, np.eye(3), atol=1e-8):
            nearest = np.clip(np.rint(origin).astype(int), 0, np.asarray(shape) - 1)
            offset = np.linalg.norm((origin - nearest) * spacing)
            # Any interpolated wall has an outside voxel centre within one
            # voxel diagonal. Subtract that and the camera's centre offset to
            # obtain a conservative sphere of guaranteed all-air samples.
            safe = float(clearance[tuple(nearest)]) - offset - np.linalg.norm(spacing)
            # EDT need not include zeros beyond the original CT. Limit the
            # sphere to the CT box as well, preserving invalid volume exits.
            safe = min(
                safe,
                float(
                    np.min(np.minimum(origin, np.asarray(shape) - 1 - origin) * spacing)
                ),
            )
            skipped = int(np.searchsorted(distances, max(0.0, safe), side="left"))
            if skipped:
                previous = float(distances[skipped - 1])
                distances = distances[skipped:]
    for distance in distances:
        indices = np.flatnonzero(active)
        if not len(indices):
            break
        hit = indices[sample(origin + distance * rays[indices]) <= 0.5]
        if len(hit):
            lower[hit] = previous
            upper[hit] = distance
            crossed[hit] = True
            active[hit] = False
        previous = distance
    # Refine every first crossing together: eight SciPy calls per image, rather
    # than eight calls for each marching step that finds new wall intersections.
    hit = np.flatnonzero(crossed)
    lower, upper = lower[hit], upper[hit]
    hit_rays = rays[hit]
    for _ in range(8):
        middle = (lower + upper) / 2
        inside = sample(origin + middle[:, None] * hit_rays) > 0.5
        lower = np.where(inside, middle, lower)
        upper = np.where(inside, upper, middle)
    intersections = (lower + upper) / 2
    wall = origin + intersections[:, None] * hit_rays
    in_volume = np.all((wall >= 0) & (wall <= np.asarray(shape) - 1), axis=1)
    depth[hit[in_volume]] = intersections[in_volume]
    valid[hit[in_volume]] = True
    return depth.reshape(size, size), valid.reshape(size, size)


def crop_airway(mask, affine):
    """Drop unused CT background; keep two voxels of halo and original CT exits."""
    occupied = [
        np.flatnonzero(np.any(mask, axis=tuple(j for j in range(3) if j != i)))
        for i in range(3)
    ]
    if any(not len(axis) for axis in occupied):
        raise ValueError("Airway segmentation is empty")
    lower = np.maximum([axis[0] - 2 for axis in occupied], 0)
    upper = np.minimum([axis[-1] + 3 for axis in occupied], mask.shape)
    slices = tuple(slice(int(a), int(b)) for a, b in zip(lower, upper))
    translation = np.eye(4)
    translation[:3, 3] = lower
    return np.ascontiguousarray(mask[slices], dtype=np.float32), np.asarray(
        affine
    ) @ translation
