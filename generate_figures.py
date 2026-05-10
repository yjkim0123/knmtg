"""Generate publication-quality figures for KNMTG paper."""
from __future__ import annotations
import sys, json, math
sys.path.insert(0, '/tmp/midpoint_project')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap
import numpy as np

from seoul_subway_data import (
    COORDS, REGIONAL_COORDS, INTERCITY_BUS_TERMINALS, BRT_HUBS,
    NODE_TYPES, LINE_SEGMENTS, KTX_SEGMENT_TIMES, REGIONAL_SEGMENTS,
    MUGUNGHWA_SEGMENTS
)

# ── Unified coord dict ──────────────────────────────────────────────
ALL_COORDS = {**COORDS, **REGIONAL_COORDS, **INTERCITY_BUS_TERMINALS, **BRT_HUBS}

# ── Korea outline (simplified polygon, lat/lng) ─────────────────────
KOREA_OUTLINE = [
    (38.6,125.1),(38.3,124.7),(37.7,124.6),(37.1,126.1),(36.1,126.4),
    (35.0,126.3),(34.4,126.7),(34.3,127.5),(34.6,128.7),(35.1,129.0),
    (35.6,129.5),(36.1,129.5),(37.0,129.4),(37.7,128.8),(38.0,128.1),
    (38.3,128.0),(38.6,128.2),(38.6,127.5),(38.6,126.5),(38.6,125.1),
]

# ── Color scheme ────────────────────────────────────────────────────
MODE_COLORS = {
    'subway':       '#2563EB',   # blue
    'ktx':          '#DC2626',   # red
    'regional_rail':'#16A34A',   # green
    'bus_terminal': '#D97706',   # amber
    'brt_hub':      '#7C3AED',   # purple
}
MODE_SIZES = {
    'subway': 8, 'ktx': 60, 'regional_rail': 28,
    'bus_terminal': 40, 'brt_hub': 35,
}
MODE_LABELS = {
    'subway':       'Subway (790)',
    'ktx':          'KTX/SRT/GTX (50)',
    'regional_rail':'Regional Rail (47)',
    'bus_terminal': 'Intercity Bus Terminal (24)',
    'brt_hub':      'BRT Hub (21)',
}
MODE_ZORDER = {
    'subway':2, 'regional_rail':3, 'bus_terminal':4, 'brt_hub':4, 'ktx':5
}

# ──────────────────────────────────────────────────────────────────
# FIGURE 1: Network Map
# ──────────────────────────────────────────────────────────────────
def make_fig1():
    fig, ax = plt.subplots(figsize=(10, 11), facecolor='#F8FAFC')
    ax.set_facecolor('#EFF6FF')

    # Korea outline fill
    poly_lons = [p[1] for p in KOREA_OUTLINE]
    poly_lats = [p[0] for p in KOREA_OUTLINE]
    ax.fill(poly_lons, poly_lats, color='#DBEAFE', alpha=0.6, zorder=0)
    ax.plot(poly_lons + [poly_lons[0]], poly_lats + [poly_lats[0]],
            color='#93C5FD', lw=1.2, zorder=1)

    # Draw subway lines (light gray)
    for line_name, stations in LINE_SEGMENTS.items():
        coords_seq = [ALL_COORDS[s] for s in stations if s in ALL_COORDS]
        if len(coords_seq) < 2:
            continue
        lons = [c[1] for c in coords_seq]
        lats = [c[0] for c in coords_seq]
        ax.plot(lons, lats, color='#CBD5E1', lw=0.35, alpha=0.7, zorder=1)

    # Draw KTX connections (light red lines)
    for (a, b), _ in KTX_SEGMENT_TIMES.items():
        if a in ALL_COORDS and b in ALL_COORDS:
            ax.plot([ALL_COORDS[a][1], ALL_COORDS[b][1]],
                    [ALL_COORDS[a][0], ALL_COORDS[b][0]],
                    color='#FCA5A5', lw=0.8, alpha=0.6, zorder=1)

    # Scatter nodes by mode
    for mode in ['subway','regional_rail','bus_terminal','brt_hub','ktx']:
        nodes = [(n, ALL_COORDS[n]) for n in ALL_COORDS
                 if NODE_TYPES.get(n) == mode and n in ALL_COORDS]
        if not nodes:
            continue
        lons = [c[1] for _, c in nodes]
        lats = [c[0] for _, c in nodes]
        ax.scatter(lons, lats,
                   c=MODE_COLORS[mode],
                   s=MODE_SIZES[mode],
                   alpha=0.85,
                   linewidths=0.3 if mode != 'ktx' else 0.6,
                   edgecolors='white' if mode == 'ktx' else 'none',
                   zorder=MODE_ZORDER[mode],
                   label=MODE_LABELS[mode])

    # City labels
    cities = {
        'Seoul':     (37.56, 126.97),
        'Busan':     (35.10, 129.02),
        'Daejeon':   (36.35, 127.38),
        'Daegu':     (35.87, 128.60),
        'Gwangju':   (35.16, 126.85),
        'Incheon':   (37.46, 126.71),
    }
    for city, (lat, lon) in cities.items():
        ax.annotate(city, (lon, lat),
                    fontsize=9, fontweight='bold',
                    color='#1E3A5F',
                    ha='center', va='bottom',
                    xytext=(0, 6), textcoords='offset points',
                    bbox=dict(boxstyle='round,pad=0.2', fc='white',
                              ec='none', alpha=0.75))

    # Legend
    handles = [
        mpatches.Patch(color=MODE_COLORS[m], label=MODE_LABELS[m])
        for m in ['subway','ktx','regional_rail','bus_terminal','brt_hub']
    ]
    ax.legend(handles=handles, loc='lower left', fontsize=8.5,
              framealpha=0.92, edgecolor='#CBD5E1', fancybox=True)

    ax.set_xlim(125.9, 130.1)
    ax.set_ylim(33.8, 38.8)
    ax.set_xlabel('Longitude', fontsize=10, color='#475569')
    ax.set_ylabel('Latitude',  fontsize=10, color='#475569')
    ax.tick_params(colors='#64748B', labelsize=8.5)
    for spine in ax.spines.values():
        spine.set_edgecolor('#CBD5E1')

    ax.set_title(
        'KNMTG v1.1 — Korean Nationwide Multimodal Transit Graph\n'
        '(932 nodes, 1,349 edges, 5 transportation modes)',
        fontsize=11, fontweight='bold', color='#1E293B', pad=12)

    plt.tight_layout()
    fig.savefig('/tmp/midpoint_project/fig1_network_map.png',
                dpi=180, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    print("fig1 saved")


# ──────────────────────────────────────────────────────────────────
# FIGURE 2: Degree Distribution
# ──────────────────────────────────────────────────────────────────
def make_fig2():
    from meeting_finder_v3 import build_graph
    G = build_graph()
    degrees = sorted([len(v) for v in G.values()])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.5), facecolor='#F8FAFC')

    # Histogram
    max_deg = max(degrees)
    bins = list(range(1, min(max_deg+2, 25)))
    counts = [degrees.count(d) for d in range(1, min(max_deg+1, 25))]
    bars = ax1.bar(range(1, min(max_deg+1, 25)), counts,
                   color='#2563EB', alpha=0.8, edgecolor='white', lw=0.5)
    ax1.axvline(np.mean(degrees), color='#DC2626', lw=1.8,
                linestyle='--', label=f'Mean={np.mean(degrees):.1f}')
    ax1.axvline(np.median(degrees), color='#D97706', lw=1.8,
                linestyle=':', label=f'Median={int(np.median(degrees))}')
    ax1.set_xlabel('Node Degree', fontsize=10)
    ax1.set_ylabel('Number of Nodes', fontsize=10)
    ax1.set_title('Degree Distribution', fontsize=11, fontweight='bold')
    ax1.legend(fontsize=9)
    ax1.set_facecolor('#F0F9FF')
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)

    # Log-log
    from collections import Counter
    deg_cnt = Counter(degrees)
    xs = sorted(deg_cnt.keys())
    ys = [deg_cnt[x] for x in xs]
    ax2.scatter(xs, ys, color='#DC2626', s=40, alpha=0.8, zorder=3)
    ax2.set_xscale('log'); ax2.set_yscale('log')
    ax2.set_xlabel('Degree (log)', fontsize=10)
    ax2.set_ylabel('Count (log)', fontsize=10)
    ax2.set_title('Degree Distribution (log-log)', fontsize=11, fontweight='bold')
    ax2.set_facecolor('#F0F9FF')
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.grid(True, alpha=0.3)

    plt.suptitle('KNMTG v1.1 — Node Degree Distribution',
                 fontsize=12, fontweight='bold', color='#1E293B', y=1.01)
    plt.tight_layout()
    fig.savefig('/tmp/midpoint_project/fig2_degree_dist.png',
                dpi=180, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    print("fig2 saved")


if __name__ == '__main__':
    make_fig1()
    make_fig2()
    print("All figures generated.")
