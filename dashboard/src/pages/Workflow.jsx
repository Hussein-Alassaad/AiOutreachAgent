import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import './Workflow.css'

/**
 * Interactive node-graph view of the agent's full task pipeline (Phase 2
 * through Phase 8), styled after n8n's node editor: icon color marks which
 * phase a step belongs to, a small corner badge marks whether it's actually
 * built and verified yet. Fully self-contained — drag to pan, scroll to
 * zoom, drag any node to reposition it.
 *
 * This started as a standalone visualisation and was moved into the real
 * dashboard so it lives under Nexaris's own deployment rather than a
 * throwaway page. The interaction logic below is plain DOM manipulation
 * inside a single effect (not React state) because nothing here needs to
 * re-render — it's the same reasoning that makes embedding a D3/canvas
 * visualisation inside a React app standard practice.
 */

const PHASES = {
  p2: { label: 'Account core', color: '#0F8B8D', icon: 'ic-shield' },
  p3: { label: 'Discovery', color: '#C98A1B', icon: 'ic-radar' },
  p4: { label: 'AI analysis', color: '#6D53C9', icon: 'ic-spark' },
  p5: { label: 'Messaging', color: '#C94F7C', icon: 'ic-mail' },
  p6: { label: 'Approval gate', color: '#4C5FD1', icon: 'ic-seal' },
  p7g: { label: 'Routing gate', color: '#4C5FD1', icon: 'ic-fork' },
  p7: { label: 'Sending', color: '#2E86D8', icon: 'ic-plane' },
  p8: { label: 'CRM & notify', color: '#12896B', icon: 'ic-stack' },
  p8n: { label: 'Notification', color: '#12896B', icon: 'ic-bell' },
}

const NODES = [
  { id: 'n1', x: 30, y: 220, shape: 'square', phase: 'p2', status: 'done', label: 'Boot: load config' },
  { id: 'n2', x: 200, y: 220, shape: 'square', phase: 'p2', status: 'done', label: 'Health check + session' },

  { id: 'n3', x: 370, y: 220, shape: 'square', phase: 'p3', status: 'pending', label: 'LinkedIn discovery' },
  { id: 'n4', x: 370, y: 400, shape: 'square', phase: 'p3', status: 'pending', label: 'Instagram discovery' },
  { id: 'n5', x: 540, y: 220, shape: 'square', phase: 'p3', status: 'done', label: 'Qualification' },

  { id: 'n6', x: 710, y: 220, shape: 'square', phase: 'p4', status: 'todo', label: 'Deep analysis' },
  { id: 'n7', x: 660, y: 390, shape: 'circle', phase: 'p4', status: 'todo', label: 'Founder detection', parent: 'n6' },
  { id: 'n8', x: 800, y: 390, shape: 'circle', phase: 'p4', status: 'todo', label: 'WhatsApp # detection', parent: 'n6' },

  { id: 'n9', x: 880, y: 220, shape: 'square', phase: 'p4', status: 'todo', label: 'Lead scoring 1–10' },
  { id: 'n11', x: 880, y: 390, shape: 'circle', phase: 'p8n', status: 'todo', label: 'Hot lead alert', parent: 'n9' },

  { id: 'n10', x: 1050, y: 220, shape: 'square', phase: 'p4', status: 'todo', label: 'Save to Client History' },

  { id: 'n12', x: 1220, y: 220, shape: 'square', phase: 'p5', status: 'todo', label: 'Message generation' },

  { id: 'n13', x: 1390, y: 220, shape: 'square', phase: 'p6', status: 'todo', label: "Mahmoud's approval" },

  { id: 'n14', x: 1560, y: 220, shape: 'square', phase: 'p7g', status: 'todo', label: 'Platform routing' },
  { id: 'n15', x: 1730, y: 90, shape: 'square', phase: 'p7', status: 'todo', label: 'Auto-send LinkedIn' },
  { id: 'n16', x: 1730, y: 220, shape: 'square', phase: 'p7', status: 'todo', label: 'Auto-send WhatsApp' },
  { id: 'n17', x: 1730, y: 350, shape: 'square', phase: 'p7', status: 'todo', label: 'Manual queue (Instagram)', manual: true },

  { id: 'n18', x: 1900, y: 220, shape: 'square', phase: 'p8', status: 'todo', label: 'CRM: New → Contacted' },
  { id: 'n19', x: 1860, y: 390, shape: 'circle', phase: 'p8n', status: 'todo', label: 'Follow-up scheduler', parent: 'n18' },
  { id: 'n20', x: 1980, y: 390, shape: 'circle', phase: 'p8n', status: 'todo', label: 'Daily summary', parent: 'n18' },
]

const MAIN_EDGES = [
  ['n1', 'n2'], ['n2', 'n3'], ['n2', 'n4'], ['n3', 'n5'], ['n4', 'n5'],
  ['n5', 'n6', 'qualifies'], ['n6', 'n9'], ['n9', 'n10'],
  ['n10', 'n12'], ['n12', 'n13'], ['n13', 'n12', 'held/edited'], ['n13', 'n14', 'approved'],
  ['n14', 'n15', 'LinkedIn'], ['n14', 'n16', 'WhatsApp #'], ['n14', 'n17', 'Instagram'],
  ['n15', 'n18'], ['n16', 'n18'], ['n17', 'n18', 'marked sent'],
]
const SUB_EDGES = [
  ['n6', 'n7'], ['n6', 'n8'], ['n9', 'n11', 'score 8–10'], ['n18', 'n19'], ['n18', 'n20'],
]

const NODE_W = 128, NODE_H = 58, SUB_W = 108, SUB_H = 44
const STATUS_GLYPH = { done: '✓', pending: '!' }

export default function Workflow() {
  const viewportRef = useRef(null)
  const worldRef = useRef(null)
  const svgRef = useRef(null)
  const phaseLegendRef = useRef(null)
  const zoomInRef = useRef(null)
  const zoomOutRef = useRef(null)
  const zoomResetRef = useRef(null)

  useEffect(() => {
    const world = worldRef.current
    const viewport = viewportRef.current
    const svg = svgRef.current
    const phaseLegendEl = phaseLegendRef.current

    const byId = {}
    NODES.forEach((n) => { byId[n.id] = n })

    // ---- phase legend, built from PHASES so it can never drift from the nodes ----
    const seen = {}
    NODES.forEach((n) => {
      if (seen[n.phase]) return
      seen[n.phase] = true
      const p = PHASES[n.phase]
      phaseLegendEl.innerHTML +=
        `<span><span class="swatch" style="background:${p.color}"><svg viewBox="0 0 24 24"><use href="#${p.icon}"></use></svg></span>${p.label}</span>`
    })

    // ---- build node elements ----
    NODES.forEach((n) => {
      const p = PHASES[n.phase]
      const el = document.createElement('div')
      el.className = 'node' + (n.shape === 'circle' ? ' sub-node' : '')
      el.style.left = n.x + 'px'
      el.style.top = n.y + 'px'
      el.style.setProperty('--glow', p.color + '55')
      el.dataset.id = n.id

      const badgeHtml = n.status === 'todo'
        ? '<span class="status-dot st-todo"></span>'
        : `<span class="status-dot st-${n.status}">${STATUS_GLYPH[n.status] || ''}</span>`

      el.innerHTML =
        `<div class="icon-badge${n.manual ? ' manual' : ''}" style="background:${p.color}">` +
          `<svg class="glyph" viewBox="0 0 24 24"><use href="#${p.icon}"></use></svg>` +
          badgeHtml +
        '</div>' +
        `<div class="node-label">${n.label}<span class="node-sub">${p.label}</span></div>`

      world.appendChild(el)
      n.el = el
    })

    function centerRight(n) {
      const w = n.shape === 'circle' ? SUB_W : NODE_W, h = n.shape === 'circle' ? SUB_H : NODE_H
      return { x: n.x + w, y: n.y + h / 2 }
    }
    function centerLeft(n) {
      const h = n.shape === 'circle' ? SUB_H : NODE_H
      return { x: n.x, y: n.y + h / 2 }
    }
    function centerTop(n) {
      const w = n.shape === 'circle' ? SUB_W : NODE_W
      return { x: n.x + w / 2, y: n.y }
    }
    function centerBottom(n) {
      const w = n.shape === 'circle' ? SUB_W : NODE_W, h = n.shape === 'circle' ? SUB_H : NODE_H
      return { x: n.x + w / 2, y: n.y + h }
    }

    function drawEdges() {
      const parts = [
        '<defs><marker id="wf-arrow" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
        '<path d="M0,0 L8,4 L0,8 Z" fill="#4A5C6C"></path></marker></defs>',
      ]

      MAIN_EDGES.forEach((e, i) => {
        const a = byId[e[0]], b = byId[e[1]], label = e[2]
        const p1 = centerRight(a), p2 = centerLeft(b)
        const bend = Math.max(50, Math.abs(p2.x - p1.x) * 0.5)
        const d = `M ${p1.x} ${p1.y} C ${p1.x + bend} ${p1.y}, ${p2.x - bend} ${p2.y}, ${p2.x} ${p2.y}`
        const pathId = `mp-${e[0]}-${e[1]}-${i}`
        const color = PHASES[b.phase].color
        parts.push(`<path id="${pathId}" class="main" d="${d}"></path>`)

        const dur = 2.2, stagger = (i % 4) * 0.5
        ;[0, dur / 2].forEach((begin) => {
          parts.push(
            `<circle r="3" class="packet" style="fill:${color};color:${color}">` +
              `<animateMotion dur="${dur}s" begin="${(begin + stagger).toFixed(2)}s" repeatCount="indefinite">` +
                `<mpath href="#${pathId}" xlink:href="#${pathId}"></mpath>` +
              '</animateMotion>' +
            '</circle>'
          )
        })

        if (label) {
          const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2 - 7
          parts.push(`<text x="${mx}" y="${my}" text-anchor="middle">${label}</text>`)
        }
      })

      SUB_EDGES.forEach((e) => {
        const a = byId[e[0]], b = byId[e[1]], label = e[2]
        const p1 = centerBottom(a), p2 = centerTop(b)
        const midY = (p1.y + p2.y) / 2
        const d = `M ${p1.x} ${p1.y} C ${p1.x} ${midY}, ${p2.x} ${midY}, ${p2.x} ${p2.y}`
        parts.push(`<path class="sub" d="${d}"></path>`)
        if (label) {
          parts.push(`<text x="${(p1.x + p2.x) / 2}" y="${midY + 4}" text-anchor="middle">${label}</text>`)
        }
      })

      svg.innerHTML = parts.join('')
    }

    drawEdges()

    // ---- pan + zoom ----
    let scale = 0.72, panX = 30, panY = 30
    let isPanning = false
    let panPointerStart = { x: 0, y: 0 }
    let panOrigin = { x: 0, y: 0 }
    let draggingNode = null
    let dragPointerStart = { x: 0, y: 0 }
    let dragNodeStart = { x: 0, y: 0 }

    function applyTransform() {
      world.style.transform = `translate(${panX}px,${panY}px) scale(${scale})`
    }
    applyTransform()

    function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)) }

    function zoomAt(cx, cy, nextScale) {
      nextScale = clamp(nextScale, 0.32, 1.9)
      const worldX = (cx - panX) / scale, worldY = (cy - panY) / scale
      scale = nextScale
      panX = cx - worldX * scale
      panY = cy - worldY * scale
      applyTransform()
    }

    function onWheel(e) {
      e.preventDefault()
      const rect = viewport.getBoundingClientRect()
      zoomAt(e.clientX - rect.left, e.clientY - rect.top, scale + (e.deltaY > 0 ? -0.08 : 0.08))
    }
    function onZoomIn() {
      const r = viewport.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, scale + 0.15)
    }
    function onZoomOut() {
      const r = viewport.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, scale - 0.15)
    }
    function onZoomReset() {
      scale = 0.72; panX = 30; panY = 30; applyTransform()
    }

    function onViewportPointerDown(e) {
      if (e.target.closest('.node')) return
      isPanning = true
      viewport.classList.add('panning')
      panPointerStart = { x: e.clientX, y: e.clientY }
      panOrigin = { x: panX, y: panY }
      viewport.setPointerCapture(e.pointerId)
    }

    function onWorldPointerDown(e) {
      const nodeEl = e.target.closest('.node')
      if (!nodeEl) return
      e.stopPropagation()
      const n = byId[nodeEl.dataset.id]
      draggingNode = n
      dragPointerStart = { x: e.clientX, y: e.clientY }
      dragNodeStart = { x: n.x, y: n.y }
      nodeEl.setPointerCapture(e.pointerId)
    }

    function onDocumentPointerMove(e) {
      if (isPanning) {
        panX = panOrigin.x + (e.clientX - panPointerStart.x)
        panY = panOrigin.y + (e.clientY - panPointerStart.y)
        applyTransform()
      } else if (draggingNode) {
        const dx = (e.clientX - dragPointerStart.x) / scale
        const dy = (e.clientY - dragPointerStart.y) / scale
        draggingNode.x = dragNodeStart.x + dx
        draggingNode.y = dragNodeStart.y + dy
        draggingNode.el.style.left = draggingNode.x + 'px'
        draggingNode.el.style.top = draggingNode.y + 'px'
        drawEdges()
      }
    }

    function onDocumentPointerUp() {
      isPanning = false
      draggingNode = null
      viewport.classList.remove('panning')
    }

    viewport.addEventListener('wheel', onWheel, { passive: false })
    viewport.addEventListener('pointerdown', onViewportPointerDown)
    world.addEventListener('pointerdown', onWorldPointerDown)
    document.addEventListener('pointermove', onDocumentPointerMove)
    document.addEventListener('pointerup', onDocumentPointerUp)
    zoomInRef.current.addEventListener('click', onZoomIn)
    zoomOutRef.current.addEventListener('click', onZoomOut)
    zoomResetRef.current.addEventListener('click', onZoomReset)

    // Cleanup: document-level listeners must be removed on unmount, or
    // navigating to /workflow more than once would stack duplicate handlers.
    return () => {
      viewport.removeEventListener('wheel', onWheel)
      document.removeEventListener('pointermove', onDocumentPointerMove)
      document.removeEventListener('pointerup', onDocumentPointerUp)
      world.innerHTML = ''
      phaseLegendEl.innerHTML = ''
    }
  }, [])

  return (
    <div className="workflow-page">
      <div className="wrap">
        <Link to="/" className="back-link">← back to status</Link>
        <header>
          <p className="eyebrow">Nexaris · AI Outreach Agent</p>
          <h1>Agent Workflow</h1>
          <p className="job">
            The complete task pipeline, Phase 2 through Phase 8 — drag nodes, pan the canvas,
            scroll to zoom. Icon color marks which phase a step belongs to; the small corner
            badge marks whether it's actually built yet.
          </p>
        </header>

        <div className="stats">
          <div className="stat done"><span className="n">3</span><span className="l">Built &amp; verified live</span></div>
          <div className="stat pending"><span className="n">2</span><span className="l">Built, pending your login</span></div>
          <div className="stat todo"><span className="n">15</span><span className="l">Not started yet</span></div>
        </div>

        <div className="legends">
          <div className="legend-group">
            <div className="legend-title">Phase (icon)</div>
            <div className="legend-row" ref={phaseLegendRef}></div>
          </div>
          <div className="legend-group">
            <div className="legend-title">Build status (badge)</div>
            <div className="legend-row">
              <span><span className="badge-demo" style={{ background: 'var(--st-done)', color: '#06210F' }}>&#10003;</span>Done, live-tested</span>
              <span><span className="badge-demo" style={{ background: 'var(--st-pending)', color: '#2A1804' }}>!</span>Built, unverified</span>
              <span><span className="badge-demo" style={{ background: '#1B222B', border: '1.5px solid #45525E' }}></span>Not started</span>
            </div>
          </div>
        </div>

        <div className="toolbar">
          <span className="hint">drag canvas to pan · scroll/pinch to zoom · drag a node to move it</span>
          <div className="zoom-controls">
            <button ref={zoomOutRef} aria-label="Zoom out" type="button">&minus;</button>
            <button ref={zoomResetRef} aria-label="Reset view" type="button">&#9678;</button>
            <button ref={zoomInRef} aria-label="Zoom in" type="button">&plus;</button>
          </div>
        </div>

        <div className="canvas-frame">
          <div className="viewport" ref={viewportRef}>
            <div className="world" ref={worldRef}>
              <svg className="edges-svg" ref={svgRef}></svg>
            </div>
          </div>
        </div>

        <footer>
          <div className="rule-chip"><b>Hard gate:</b> nothing crosses the approval node without Mahmoud clearing it — no bypass exists in the design.</div>
          <div className="rule-chip"><b>Instagram never auto-sends</b> (dashed icon border): the routing node only ever queues it for a human to send by hand.</div>
        </footer>
      </div>

      <svg width="0" height="0" style={{ position: 'absolute' }}>
        <defs>
          <symbol id="ic-shield" viewBox="0 0 24 24">
            <path d="M12 2.5 L20 6 V12 C20 17 16.2 20.3 12 21.7 C7.8 20.3 4 17 4 12 V6 Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
            <path d="M8.2 12.2 L10.6 14.6 L15.8 9.2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </symbol>
          <symbol id="ic-radar" viewBox="0 0 24 24">
            <circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <line x1="15.2" y1="15.2" x2="21" y2="21" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </symbol>
          <symbol id="ic-spark" viewBox="0 0 24 24">
            <path d="M12 2 L14.2 9.8 L22 12 L14.2 14.2 L12 22 L9.8 14.2 L2 12 L9.8 9.8 Z" fill="currentColor" />
          </symbol>
          <symbol id="ic-mail" viewBox="0 0 24 24">
            <rect x="3" y="6" width="18" height="13" rx="2.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <path d="M3.5 7 L12 14 L20.5 7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </symbol>
          <symbol id="ic-seal" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <path d="M7.8 12.3 L10.4 15 L16.2 8.8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </symbol>
          <symbol id="ic-fork" viewBox="0 0 24 24">
            <path d="M12 3.5 V10 M12 10 L6 17 M12 10 L18 17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            <circle cx="12" cy="3.5" r="1.7" fill="currentColor" />
            <circle cx="6" cy="18" r="1.7" fill="currentColor" />
            <circle cx="18" cy="18" r="1.7" fill="currentColor" />
          </symbol>
          <symbol id="ic-plane" viewBox="0 0 24 24">
            <path d="M3 11.5 L21 3 L14 21 L11 13 Z" fill="currentColor" />
            <path d="M11 13 L21 3" fill="none" stroke="#0A141C" strokeWidth="1" opacity="0.4" />
          </symbol>
          <symbol id="ic-stack" viewBox="0 0 24 24">
            <path d="M12 3 L21 8 L12 13 L3 8 Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
            <path d="M3 13 L12 18 L21 13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M3 10.5 L12 15.5 L21 10.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity="0.55" />
          </symbol>
          <symbol id="ic-bell" viewBox="0 0 24 24">
            <path d="M12 3 C9.2 3 7.2 5.1 7.2 8.6 V12.8 L4.5 17 H19.5 L16.8 12.8 V8.6 C16.8 5.1 14.8 3 12 3 Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
            <path d="M9.3 19.5 A2.9 2.9 0 0 0 14.7 19.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </symbol>
        </defs>
      </svg>
    </div>
  )
}
