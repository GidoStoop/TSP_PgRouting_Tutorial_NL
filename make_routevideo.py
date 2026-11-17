
#!/usr/bin/env python3
"""
Create a square animated route video from a GeoPackage.

Install:
    pip install geopandas matplotlib numpy contextily pillow rasterio pyproj pyogrio

FFmpeg is required for MP4 output.

Example:
    python make_routevideo.py tsp.gpkg \
        --layer route_edges \
        --output mijn_route_square.mp4 \
        --title "MY ROUTE" \
        --subtitle "A journey through the network" \
        --fps 30 \
        --seconds 60

Visited-node markers:
    A white dot is placed at the START of each line segment where:
        tsp_seq == 1 AND path_seq == 1

Distance is measured in the configured projected CRS.
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
import numpy as np
import contextily as cx

from pyproj import CRS
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter
from matplotlib.patches import FancyBboxPatch


# ---------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------

BACKGROUND = "#101714"
PANEL = "#1b2721"
ROUTE_COLOR = "#d7ff65"
START_COLOR = "#55e878"
END_COLOR = "#ff5757"
NODE_COLOR = "#ffffff"
TEXT_COLOR = "#f1f3e9"
MUTED_COLOR = "#b8c6bc"

BASEMAP_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)


# ---------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------

def extract_lines(geometry):
    """Return coordinate arrays for all LineString parts."""
    if geometry is None or geometry.is_empty:
        return []

    if geometry.geom_type == "LineString":
        return [np.asarray(geometry.coords, dtype=float)[:, :2]]

    if geometry.geom_type in ("MultiLineString", "GeometryCollection"):
        result = []
        for part in geometry.geoms:
            result.extend(extract_lines(part))
        return result

    return []


def line_start_points(geometry):
    """
    Return the start coordinate of each line feature.

    For a LineString, this is its first coordinate.
    For a MultiLineString, use the first coordinate of its first
    non-empty line part.
    """
    lines = extract_lines(geometry)

    if not lines:
        return []

    # A MultiLineString usually represents one feature. Use its first
    # non-empty component to identify the feature's start.
    return [lines[0][0]]


def format_time(seconds):
    """Format seconds as HH:MM:SS."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def ease_in_out(progress):
    """Smooth acceleration and deceleration."""
    progress = np.clip(progress, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * progress)


def validate_measure_crs(crs_input):
    """Ensure distance measurements use a projected CRS in metres."""
    crs = CRS.from_user_input(crs_input)

    if not crs.is_projected:
        raise ValueError(
            f"{crs_input} is not a projected CRS. "
            "Use a projected CRS such as EPSG:28992."
        )

    axes = crs.axis_info[:2]
    if len(axes) < 2 or any(
        axis.unit_name.lower() not in (
            "metre", "meter", "metres", "meters"
        )
        for axis in axes
    ):
        raise ValueError(
            f"{crs_input} does not use metres for its axis units."
        )

    return crs


# ---------------------------------------------------------------------
# Load route and build animation coordinates
# ---------------------------------------------------------------------

def load_route(gpkg, layer, measure_crs, agg_cost_mode):
    """Load and order route segments, measuring them in metres."""
    kwargs = {"layer": layer} if layer else {}
    gdf = gpd.read_file(gpkg, **kwargs)

    if gdf.empty:
        raise ValueError("The selected route layer is empty.")

    if gdf.crs is None:
        raise ValueError("The route layer has no CRS.")

    if "agg_cost" not in gdf.columns:
        raise ValueError("The route layer must contain 'agg_cost'.")

    # Respect route ordering when these columns are available.
    sort_cols = [
        col for col in ("tsp_seq", "path_seq", "seq")
        if col in gdf.columns
    ]
    if sort_cols:
        gdf = gdf.sort_values(sort_cols, kind="stable").reset_index(
            drop=True
        )

    measured = gdf.to_crs(measure_crs)

    route_points = []
    route_distances = []
    route_times = []

    total_distance = 0.0
    previous_point = None
    previous_time = 0.0

    for _, row in measured.iterrows():
        raw_cost = row["agg_cost"]

        if raw_cost is None or not np.isfinite(raw_cost):
            raise ValueError(
                "Invalid or missing value found in agg_cost."
            )

        cost = max(float(raw_cost), 0.0)
        lines = extract_lines(row.geometry)

        if not lines:
            continue

        # Assemble line parts in their stored order.
        feature_coords = []
        for line in lines:
            if len(line) < 2:
                continue

            if (
                feature_coords
                and np.linalg.norm(line[0] - feature_coords[-1]) < 0.001
            ):
                feature_coords.extend(line[1:])
            else:
                feature_coords.extend(line)

        if len(feature_coords) < 2:
            continue

        feature_coords = np.asarray(feature_coords, dtype=float)

        step_lengths = np.linalg.norm(
            np.diff(feature_coords, axis=0),
            axis=1,
        )
        local_distances = np.concatenate([
            [0.0],
            np.cumsum(step_lengths),
        ])

        feature_length = float(local_distances[-1])
        if feature_length <= 0:
            continue

        if agg_cost_mode == "cumulative":
            feature_start_time = previous_time
            feature_end_time = max(cost, feature_start_time)
        else:
            feature_start_time = previous_time
            feature_end_time = feature_start_time + cost

        for i, point in enumerate(feature_coords):
            point = np.asarray(point, dtype=float)

            if previous_point is not None:
                step = float(np.linalg.norm(point - previous_point))

                # Avoid duplicate vertices between adjoining segments.
                if step < 0.001:
                    continue

                total_distance += step

            fraction = local_distances[i] / feature_length
            point_time = (
                feature_start_time
                + fraction * (feature_end_time - feature_start_time)
            )

            route_points.append(point)
            route_distances.append(total_distance)
            route_times.append(point_time)

            previous_point = point

        previous_time = feature_end_time

    if len(route_points) < 2 or total_distance <= 0:
        raise ValueError(
            "Could not build a valid route from the selected line layer."
        )

    route_points = np.asarray(route_points, dtype=float)
    route_distances = np.asarray(route_distances, dtype=float)
    route_times = np.maximum.accumulate(
        np.asarray(route_times, dtype=float)
    )

    return (
        gdf,
        route_points,
        route_distances,
        route_times,
        total_distance,
        float(route_times[-1]),
    )


# ---------------------------------------------------------------------
# Extract visited nodes from line-segment start points
# ---------------------------------------------------------------------

def load_visited_nodes(gdf, display_crs):
    """
    Find line features where tsp_seq == 1 AND path_seq == 1.

    Each selected feature contributes its start coordinate. The
    coordinates are transformed into the display CRS and deduplicated.
    """
    required = {"tsp_seq", "path_seq"}

    if not required.issubset(gdf.columns):
        raise ValueError(
            "The route layer must contain both 'tsp_seq' and 'path_seq' "
            "to identify visited nodes."
        )

    selected = gdf.loc[
        (gdf["path_seq"] == 1)
    ].copy()

    print(
        f"Features matching path_seq == 1: "
        f"{len(selected)}",
        flush=True,
    )

    if selected.empty:
        print(
            "Warning: no features matched both sequence conditions. "
            "Check the values in tsp_seq and path_seq.",
            flush=True,
        )
        return np.empty((0, 2), dtype=float)

    # Transform line features before extracting their start coordinates.
    selected = selected.to_crs(display_crs)

    starts = []
    for geometry in selected.geometry:
        starts.extend(line_start_points(geometry))

    if not starts:
        print(
            "Warning: matching features did not contain usable lines.",
            flush=True,
        )
        return np.empty((0, 2), dtype=float)

    # One marker per unique start coordinate.
    starts = np.asarray(starts, dtype=float)
    starts = np.unique(np.round(starts, decimals=3), axis=0)

    print(f"Unique visited-node coordinates: {len(starts)}", flush=True)

    return starts


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Create a square animated route video."
    )

    parser.add_argument("gpkg", type=Path, help="Input GeoPackage")
    parser.add_argument("--layer", help="Route line layer name")

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mijn_route_square.mp4"),
        help="Output filename (.mp4 or .gif)",
    )

    parser.add_argument(
        "--crs",
        default="EPSG:3857",
        help="Display CRS (default EPSG:3857)",
    )
    parser.add_argument(
        "--measure-crs",
        default="EPSG:28992",
        help="Projected metre CRS for distance calculations",
    )

    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--dpi", type=int, default=160)

    parser.add_argument("--title", default="MY ROUTE")
    parser.add_argument("--subtitle", default="A JOURNEY THROUGH THE NETWORK")

    parser.add_argument(
        "--agg-cost-mode",
        choices=("cumulative", "segment"),
        default="cumulative",
        help=(
            "'cumulative' if agg_cost is cumulative elapsed time; "
            "'segment' if agg_cost is seconds per feature"
        ),
    )

    parser.add_argument(
        "--basemap-url",
        default=BASEMAP_URL,
        help="XYZ basemap tile URL",
    )
    parser.add_argument(
        "--no-basemap",
        action="store_true",
        help="Disable basemap downloading",
    )

    args = parser.parse_args()

    if not args.gpkg.exists():
        parser.error(f"Input file not found: {args.gpkg}")

    if args.fps < 1 or args.seconds < 1 or args.dpi < 1:
        parser.error("FPS, seconds, and DPI must be positive.")

    try:
        display_crs = CRS.from_user_input(args.crs)
        measure_crs = validate_measure_crs(args.measure_crs)
    except Exception as exc:
        parser.error(f"Invalid CRS: {exc}")

    print(f"Input file: {args.gpkg}", flush=True)
    print(f"Display CRS: {display_crs.to_string()}", flush=True)
    print(f"Measurement CRS: {measure_crs.to_string()}", flush=True)

    try:
        (
            route_gdf,
            points_measure,
            distances,
            elapsed_times,
            total_distance,
            total_seconds,
        ) = load_route(
            args.gpkg,
            args.layer,
            measure_crs,
            args.agg_cost_mode,
        )

        node_xy = load_visited_nodes(route_gdf, display_crs)

    except Exception as exc:
        raise SystemExit(f"Could not prepare route: {exc}") from exc

    # Convert the animation coordinates to the display CRS.
    route_points_display = gpd.GeoSeries(
        gpd.points_from_xy(
            points_measure[:, 0],
            points_measure[:, 1],
        ),
        crs=measure_crs,
    ).to_crs(display_crs)

    points_display = np.column_stack([
        route_points_display.x.to_numpy(),
        route_points_display.y.to_numpy(),
    ])

    route_plot = route_gdf.to_crs(display_crs)

    print(
        f"Route length: {total_distance / 1000:.2f} km",
        flush=True,
    )
    print(
        f"Total elapsed time: {format_time(total_seconds)}",
        flush=True,
    )

    # -------------------------------------------------------------
    # Square canvas and map extent
    # -------------------------------------------------------------

    minx, miny, maxx, maxy = route_plot.total_bounds

    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    center_x = (minx + maxx) / 2
    center_y = (miny + maxy) / 2

    # Make the geographic extent square so the route remains visible.
    square_extent = max(width, height) * 1.12
    half_extent = square_extent / 2

    xlim = (center_x - half_extent, center_x + half_extent)
    ylim = (center_y - half_extent, center_y + half_extent)

    # Square video: 1080 x 1080 at the default 160 DPI.
    fig, ax = plt.subplots(
        figsize=(6.75, 6.75),
        facecolor=BACKGROUND,
    )

    # Reserve a compact header and footer without shrinking the map
    # excessively.
    fig.subplots_adjust(
        left=0.025,
        right=0.975,
        top=0.865,
        bottom=0.165,
    )

    ax.set_facecolor(BACKGROUND)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")

    if not args.no_basemap:
        print("Loading basemap...", flush=True)
        try:
            cx.add_basemap(
                ax,
                source=args.basemap_url,
                crs=display_crs.to_string(),
                attribution_size=5,
                zorder=0,
            )
        except Exception as exc:
            plt.close(fig)
            raise SystemExit(
                "Could not load the basemap. Check your connection, "
                "or rerun with --no-basemap. "
                f"Details: {exc}"
            ) from exc

    # Draw all line features as a subtle background network.
    for geometry in route_plot.geometry:
        for line in extract_lines(geometry):
            ax.plot(
                line[:, 0],
                line[:, 1],
                color="#c1ccc5",
                linewidth=0.65,
                alpha=0.55,
                zorder=2,
            )


    # -------------------------------------------------------------
    # Animated route line and moving marker
    # -------------------------------------------------------------

    travelled_line, = ax.plot(
        [],
        [],
        color=ROUTE_COLOR,
        linewidth=1.7,
        solid_capstyle="round",
        zorder=7,
    )

    moving_marker = ax.scatter(
        [],
        [],
        s=65,
        c=ROUTE_COLOR,
        edgecolors=BACKGROUND,
        linewidths=1.2,
        zorder=8,
    )

        # Draw only the matching line-start nodes as white dots.
    if len(node_xy):
        ax.scatter(
            node_xy[:, 0],
            node_xy[:, 1],
            s=7.5,
            c=NODE_COLOR,
            edgecolors=BACKGROUND,
            linewidths=1,
            alpha=1.0,
            zorder=11,
        )


    # Start and finish flags use tiny offsets in screen points.
    start_x, start_y = points_display[0]
    end_x, end_y = points_display[-1]

    flag_effects = [
        path_effects.Stroke(
            linewidth=1.3,
            foreground=BACKGROUND,
        ),
        path_effects.Normal(),
    ]

    start_flag = ax.annotate(
        "\u2691",
        xy=(start_x, start_y),
        xytext=(-4, 0),
        textcoords="offset points",
        color=START_COLOR,
        fontsize=19,
        ha="center",
        va="center",
        zorder=12,
    )
    start_flag.set_path_effects(flag_effects)

    end_flag = ax.annotate(
        "\u2691",
        xy=(end_x, end_y),
        xytext=(4, 0),
        textcoords="offset points",
        color=END_COLOR,
        fontsize=19,
        ha="center",
        va="center",
        zorder=12,
    )
    end_flag.set_path_effects(flag_effects)

    # -------------------------------------------------------------
    # Compact square-video header
    # -------------------------------------------------------------

    fig.text(
        0.045,
        0.955,
        args.title.upper(),
        color=ROUTE_COLOR,
        fontsize=17,
        fontweight="bold",
        ha="left",
    )

    fig.text(
        0.047,
        0.922,
        args.subtitle,
        color=MUTED_COLOR,
        fontsize=9,
        ha="left",
    )

    progress_text = fig.text(
        0.955,
        0.953,
        "0%",
        color=TEXT_COLOR,
        fontsize=15,
        fontweight="bold",
        ha="right",
    )

    # Compact statistic panels along the bottom.
    stats = []

    panel_specs = [
        (0.045, "DISTANCE", ROUTE_COLOR, "0.0 km"),
        (0.525, "ELAPSED TIME", TEXT_COLOR, "00:00:00"),
    ]

    for x, label, color, initial_value in panel_specs:
        panel = FancyBboxPatch(
            (x, 0.035),
            0.43,
            0.095,
            boxstyle="round,pad=0.006,rounding_size=0.012",
            transform=fig.transFigure,
            facecolor=PANEL,
            edgecolor="#34443a",
            linewidth=0.7,
            zorder=10,
        )
        fig.patches.append(panel)

        fig.text(
            x + 0.016,
            0.101,
            label,
            color=MUTED_COLOR,
            fontsize=8,
            fontweight="bold",
            ha="left",
            zorder=11,
        )

        value = fig.text(
            x + 0.016,
            0.058,
            initial_value,
            color=color,
            fontsize=16,
            fontweight="bold",
            ha="left",
            zorder=11,
        )
        stats.append(value)

    fig.text(
        0.975,
        0.008,
        "Esri basemap",
        color=MUTED_COLOR,
        fontsize=5,
        ha="right",
    )

    # -------------------------------------------------------------
    # Frame timing and animation
    # -------------------------------------------------------------

    frame_count = args.fps * args.seconds
    linear_progress = np.linspace(0.0, 1.0, frame_count)
    eased_progress = ease_in_out(linear_progress)

    target_distances = eased_progress * total_distance

    frame_xy = np.column_stack([
        np.interp(
            target_distances,
            distances,
            points_display[:, 0],
        ),
        np.interp(
            target_distances,
            distances,
            points_display[:, 1],
        ),
    ])

    frame_times = np.interp(
        target_distances,
        distances,
        elapsed_times,
    )

    def update(frame):
        target = target_distances[frame]
        x_now, y_now = frame_xy[frame]

        index = int(
            np.searchsorted(distances, target, side="right")
        )
        index = min(max(index, 1), len(points_display) - 1)

        trace = np.vstack([
            points_display[:index],
            [x_now, y_now],
        ])

        travelled_line.set_data(trace[:, 0], trace[:, 1])
        moving_marker.set_offsets([[x_now, y_now]])

        stats[0].set_text(f"{target / 1000:,.1f} km")
        stats[1].set_text(format_time(frame_times[frame]))
        progress_text.set_text(
            f"{eased_progress[frame] * 100:.0f}%"
        )

        return (
            travelled_line,
            moving_marker,
            progress_text,
            *stats,
        )

    animation = FuncAnimation(
        fig,
        update,
        frames=frame_count,
        interval=1000 / args.fps,
        blit=False,
    )

    # -------------------------------------------------------------
    # Export
    # -------------------------------------------------------------

    args.output.parent.mkdir(parents=True, exist_ok=True)
    suffix = args.output.suffix.lower()

    try:
        if suffix == ".gif":
            writer = PillowWriter(fps=args.fps)

        elif suffix == ".mp4":
            if not FFMpegWriter.isAvailable():
                raise RuntimeError(
                    "FFmpeg is unavailable. Install FFmpeg or output a GIF."
                )

            writer = FFMpegWriter(
                fps=args.fps,
                codec="libx264",
                bitrate=6000,
                metadata={"title": args.title},
                extra_args=[
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                ],
            )

        else:
            raise ValueError("Output filename must end in .mp4 or .gif.")

        print(
            f"Rendering {frame_count} frames to {args.output}...",
            flush=True,
        )

        animation.save(
            args.output,
            writer=writer,
            dpi=args.dpi,
        )

    except Exception as exc:
        plt.close(fig)
        raise SystemExit(f"Rendering failed: {exc}") from exc

    plt.close(fig)

    print(f"Video saved: {args.output.resolve()}")
    print(f"Resolution: {int(6.75 * args.dpi)} x {int(6.75 * args.dpi)}")
    print(f"Distance: {total_distance / 1000:.2f} km")
    print(f"Elapsed time: {format_time(total_seconds)}")


if __name__ == "__main__":
    main()
