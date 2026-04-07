import os
import re
import subprocess
import sys
import json
import statistics
from tqdm import tqdm
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio

BENCH_DIR_NAME = "orca_benchmarking"
SLURM_FILE_REGEX = re.compile(r"slurm-(\d+)_(\d+)\.out")

# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
    diis, soscf, geom = [], [], []
    in_diis = in_soscf = False

    iter_re = re.compile(r"^\s*\d+.*\s+([0-9]+(?:\.[0-9]+)?)\s*$")
    geom_re = re.compile(
        r"Time for complete geometry iter\s*:\s*([0-9]+(?:\.[0-9]+)?)"
    )

    with open(path) as f:
        for line in f:
            if not line.strip():
                in_diis = in_soscf = False
                continue
            if "D-I-I-S" in line:
                in_diis, in_soscf = True, False
                continue
            if "S-O-S-C-F" in line:
                in_diis, in_soscf = False, True
                continue
            if line.startswith("---"):
                continue

            g = geom_re.search(line)
            if g:
                geom.append(float(g.group(1)))
                continue

            m = iter_re.match(line)
            if m:
                val = float(m.group(1))
                if in_diis:
                    diis.append(val)
                elif in_soscf:
                    soscf.append(val)

    mean = lambda v: statistics.mean(v) if v else None
    return mean(diis), mean(soscf), mean(geom)

# ------------------------------------------------------------
# sacct helpers
# ------------------------------------------------------------
def run_sacct(jobid, taskid):
    result = subprocess.run(
        ["sacct", "--json", "-j", f"{jobid}_{taskid}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)

def parse_sacct_data(data):
    job = data["jobs"][0]
    elapsed = float(job["time"]["elapsed"])
    cpu_msec = 0
    max_mem_b = 0

    for step in job["steps"]:
        for t in step["tres"]["requested"]["total"]:
            if t["type"] == "cpu":
                cpu_msec += t["count"]
            elif t["type"] == "mem":
                max_mem_b = max(max_mem_b, t["count"])

    return elapsed, cpu_msec / 1000.0, max_mem_b / 1024.0 / 1024.0  # MB

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    print("🔍 ORCA optimisation benchmarking report")

    bench_dir = os.path.join(os.getcwd(), BENCH_DIR_NAME)
    if not os.path.isdir(bench_dir):
        sys.exit("❌ Run from the directory ABOVE orca_benchmarking/")

    print("📊 Collecting benchmark results...")
    results = []

    for fname in tqdm(sorted(os.listdir(bench_dir)), desc="Processing SLURM outputs"):
        m = SLURM_FILE_REGEX.match(fname)
        if not m:
            continue

        jobid, cores = m.group(1), int(m.group(2))
        orca_out = os.path.join(bench_dir, f"{cores}cores", "orca.out")
        if not os.path.isfile(orca_out):
            continue

        diis, soscf, geom = parse_orca_output(orca_out)
        elapsed, cpu, rss = parse_sacct_data(run_sacct(jobid, cores))
        cpu_eff = (cpu / (elapsed * cores)) * 100.0

        results.append(
            dict(
                cores=cores,
                cpu_eff=cpu_eff,
                rss=rss,
                diis=diis,
                soscf=soscf,
                geom=geom,
            )
        )

    results.sort(key=lambda r: r["cores"])
    cores = [r["cores"] for r in results]

    print("📐 Computing speedups...")
    r1 = next((r for r in results if r["cores"] == 1), None)

    speedup = {}
    if r1:
        for k in ("diis", "soscf", "geom"):
            if r1[k] is not None:
                speedup[k] = [r1[k] / r[k] if r[k] else None for r in results]
            else:
                speedup[k] = None
    else:
        speedup["diis"] = speedup["soscf"] = speedup["geom"] = None

    print("📈 Building combined figure...")

    fig = make_subplots(
        rows=3,
        cols=3,
        specs=[
            [{"type": "xy"}, {"type": "xy"}, None],
            [{"type": "xy"}, {"type": "xy"}, {"type": "xy"}],
            [{"type": "xy"}, {"type": "xy"}, {"type": "xy"}],
        ],
        subplot_titles=[
            "CPU efficiency", "Memory usage",
            "DIIS time", "SOSCF time", "Geometry time",
            "DIIS speedup", "SOSCF speedup", "Geometry speedup",
        ],
        vertical_spacing=0.06,
        horizontal_spacing=0.06,
    )

    # Row 1
    fig.add_trace(go.Scatter(x=cores, y=[r["cpu_eff"] for r in results],
                             mode="lines+markers"), 1, 1)
    fig.add_trace(go.Scatter(x=cores, y=[r["rss"] for r in results],
                             mode="lines+markers"), 1, 2)

    # Row 2 – time
    fig.add_trace(go.Scatter(x=cores, y=[r["diis"] for r in results],
                             mode="lines+markers"), 2, 1)
    fig.add_trace(go.Scatter(x=cores, y=[r["soscf"] for r in results],
                             mode="lines+markers"), 2, 2)
    fig.add_trace(go.Scatter(x=cores, y=[r["geom"] for r in results],
                             mode="lines+markers"), 2, 3)

    # Row 3 – speedup (ALL THREE)
    if speedup["diis"]:
        fig.add_trace(go.Scatter(x=cores, y=speedup["diis"],
                                 mode="lines+markers"), 3, 1)
    if speedup["soscf"]:
        fig.add_trace(go.Scatter(x=cores, y=speedup["soscf"],
                                 mode="lines+markers"), 3, 2)
    if speedup["geom"]:
        fig.add_trace(go.Scatter(x=cores, y=speedup["geom"],
                                 mode="lines+markers"), 3, 3)

    # Axis styling
    fig.update_xaxes(
        title_text="Number of cores",
        range=[0, max(cores)],
        showline=True,
        linecolor="black",
        ticks="outside",
        showticklabels=True,
    )

    fig.update_yaxes(
        rangemode="tozero",
        showline=True,
        linecolor="black",
        ticks="outside",
        showticklabels=True,
    )

    fig.update_yaxes(range=[0, 100], title_text="CPU efficiency (%)", row=1, col=1)
    fig.update_yaxes(title_text="Max RSS (MB)", row=1, col=2)

    fig.update_yaxes(title_text="Time (s)", row=2, col=1)
    fig.update_yaxes(title_text="Time (s)", row=2, col=2)
    fig.update_yaxes(title_text="Time (s)", row=2, col=3)

    fig.update_yaxes(title_text="Speedup", row=3, col=1)
    fig.update_yaxes(title_text="Speedup", row=3, col=2)
    fig.update_yaxes(title_text="Speedup", row=3, col=3)

    fig.update_layout(
        template="none",
        hovermode="x unified",
        showlegend=False,
        margin=dict(l=70, r=40, t=90, b=70),
    )

    print("🖥️ Writing combined HTML output...")

    post_script = """
    function resizeSquare() {
        const s = Math.min(window.innerWidth / 3, window.innerHeight / 2);
        Plotly.relayout('{plot_id}', {width: s * 3, height: s * 2});
    }
    window.addEventListener('resize', resizeSquare);
    resizeSquare();
    """

    html = pio.to_html(
        fig,
        full_html=True,
        include_plotlyjs="cdn",
        config={"responsive": True},
        post_script=post_script,
    )

    with open("orca_benchmark_results_opt.html", "w") as f:
        f.write(html)

    print("✅ Plot written to orca_benchmark_results_opt.html")