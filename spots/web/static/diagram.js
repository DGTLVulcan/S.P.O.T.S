// Shot-group diagram, shared by the live dashboard and the session history
// detail page so the two can't drift apart. Draws into an existing <svg>
// with a 0 0 200 200 viewBox; styling comes from .target-diagram in style.css.
//
// shots:  [{x_units, y_units, is_test}]
// center: [x_units, y_units] or null
function renderTargetDiagram(svgId, shots, center) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const ns = "http://www.w3.org/2000/svg";
  svg.innerHTML = "";
  const size = 200,
    cx = size / 2,
    cy = size / 2,
    plotRadius = size / 2 - 20;

  if (!shots.length) {
    const text = document.createElementNS(ns, "text");
    text.setAttribute("x", cx);
    text.setAttribute("y", cy);
    text.setAttribute("class", "empty-label");
    text.textContent = "No calibrated shots yet";
    svg.appendChild(text);
    return;
  }

  let maxAbs = 0.5; // floor so a single near-center shot doesn't blow up the scale
  for (const s of shots) {
    maxAbs = Math.max(maxAbs, Math.abs(s.x_units), Math.abs(s.y_units));
  }
  if (center) maxAbs = Math.max(maxAbs, Math.abs(center[0]), Math.abs(center[1]));
  const scale = plotRadius / (maxAbs * 1.15);
  const toSvg = (x, y) => [cx + x * scale, cy - y * scale];

  for (const frac of [0.33, 0.66, 1.0]) {
    const ring = document.createElementNS(ns, "circle");
    ring.setAttribute("cx", cx);
    ring.setAttribute("cy", cy);
    ring.setAttribute("r", plotRadius * frac);
    ring.setAttribute("class", "ring");
    svg.appendChild(ring);
  }
  const hline = document.createElementNS(ns, "line");
  hline.setAttribute("x1", 10);
  hline.setAttribute("x2", size - 10);
  hline.setAttribute("y1", cy);
  hline.setAttribute("y2", cy);
  hline.setAttribute("class", "crosshair");
  svg.appendChild(hline);
  const vline = document.createElementNS(ns, "line");
  vline.setAttribute("y1", 10);
  vline.setAttribute("y2", size - 10);
  vline.setAttribute("x1", cx);
  vline.setAttribute("x2", cx);
  vline.setAttribute("class", "crosshair");
  svg.appendChild(vline);

  for (const s of shots) {
    const [x, y] = toSvg(s.x_units, s.y_units);
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    dot.setAttribute("r", 5);
    const cls = ["shot"];
    if (s.is_test) cls.push("shot-test");
    if (s.excluded) cls.push("shot-excluded");
    dot.setAttribute("class", cls.join(" "));
    svg.appendChild(dot);
  }

  if (center) {
    const [x, y] = toSvg(center[0], center[1]);
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", x);
    c.setAttribute("cy", y);
    c.setAttribute("r", 7);
    c.setAttribute("class", "center");
    svg.appendChild(c);
  }
}
