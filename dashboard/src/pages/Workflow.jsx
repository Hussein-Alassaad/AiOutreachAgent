import { useEffect, useRef, useState } from 'react'
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

  { id: 'n13', x: 1390, y: 220, shape: 'square', phase: 'p6', status: 'todo', label: "Mohamad's approval" },

  { id: 'n14', x: 1560, y: 220, shape: 'square', phase: 'p7g', status: 'todo', label: 'Platform routing' },
  { id: 'n15', x: 1730, y: 90, shape: 'square', phase: 'p7', status: 'todo', label: 'Auto-send LinkedIn' },
  { id: 'n16', x: 1730, y: 220, shape: 'square', phase: 'p7', status: 'todo', label: 'Auto-send WhatsApp' },
  { id: 'n17', x: 1730, y: 350, shape: 'square', phase: 'p7', status: 'todo', label: 'Manual queue (Instagram)', manual: true },

  { id: 'n18', x: 1900, y: 220, shape: 'square', phase: 'p8', status: 'todo', label: 'CRM: New → Contacted' },
  { id: 'n19', x: 1860, y: 390, shape: 'circle', phase: 'p8n', status: 'todo', label: 'Follow-up scheduler', parent: 'n18' },
  { id: 'n20', x: 1980, y: 390, shape: 'circle', phase: 'p8n', status: 'todo', label: 'Daily summary', parent: 'n18' },
]

// A 4th element of 'vertical' means exit/enter the node from top or bottom
// instead of right/left -- without it, these three edges' curves swing out
// just far enough to cut straight through the sibling node sitting between
// source and target at the same height (n3 for n2->n4; n16 for the n14
// fan-out, since n14/n15/n17 are only ~40px apart horizontally and the
// curve's control point overshoots into whatever sits at that x, y).
const MAIN_EDGES = [
  ['n1', 'n2'], ['n2', 'n3'], ['n2', 'n4', null, 'vertical'], ['n3', 'n5'], ['n4', 'n5'],
  ['n5', 'n6', 'qualifies'], ['n6', 'n9'], ['n9', 'n10'],
  ['n10', 'n12'], ['n12', 'n13'], ['n13', 'n12', 'held/edited'], ['n13', 'n14', 'approved'],
  ['n14', 'n15', 'LinkedIn', 'vertical'], ['n14', 'n16', 'WhatsApp #'], ['n14', 'n17', 'Instagram', 'vertical'],
  ['n15', 'n18'], ['n16', 'n18'], ['n17', 'n18', 'marked sent'],
]
const SUB_EDGES = [
  ['n6', 'n7'], ['n6', 'n8'], ['n9', 'n11', 'score 8–10'], ['n18', 'n19'], ['n18', 'n20'],
]

const NODE_W = 128, NODE_H = 58, SUB_W = 108, SUB_H = 44
const STATUS_GLYPH = { done: '✓', pending: '!' }

export default function Workflow() {
  const pageRef = useRef(null)
  const viewportRef = useRef(null)
  const worldRef = useRef(null)
  const svgRef = useRef(null)
  const phaseLegendRef = useRef(null)
  const zoomInRef = useRef(null)
  const zoomOutRef = useRef(null)
  const zoomResetRef = useRef(null)

  // Both toggles act directly on the live DOM via refs/classList rather than
  // re-running the whole draw effect -- flipping them shouldn't reset pan,
  // zoom, or any node you've dragged. animOnRef is what the packet-dot rAF
  // loop (inside the effect below) actually reads each frame; the state
  // versions only exist to drive the buttons' own pressed/label look.
  const animOnRef = useRef(true)
  const [solid, setSolid] = useState(false)
  const [animOn, setAnimOn] = useState(true)

  function toggleSolid() {
    setSolid((s) => {
      const next = !s
      viewportRef.current?.classList.toggle('wf-solid', next)
      return next
    })
  }
  function toggleAnimations() {
    setAnimOn((a) => {
      const next = !a
      animOnRef.current = next
      pageRef.current?.classList.toggle('wf-no-anim', !next)
      return next
    })
  }

  useEffect(() => {
    const world = worldRef.current
    const viewport = viewportRef.current
    const svg = svgRef.current
    const phaseLegendEl = phaseLegendRef.current

    const byId = {}
    NODES.forEach((n) => { byId[n.id] = n })

    // ---- adjacency map (static, independent of node position) -- powers
    // the hover-to-trace-the-path effect: every node knows which other
    // nodes and which edge keys touch it. ----
    const adjacency = {}
    function addAdj(from, to, edgeKey) {
      if (!adjacency[from]) adjacency[from] = { nodes: new Set(), edges: new Set() }
      if (!adjacency[to]) adjacency[to] = { nodes: new Set(), edges: new Set() }
      adjacency[from].nodes.add(to)
      adjacency[from].edges.add(edgeKey)
      adjacency[to].nodes.add(from)
      adjacency[to].edges.add(edgeKey)
    }
    MAIN_EDGES.forEach(([from, to]) => addAdj(from, to, `${from}>${to}`))
    SUB_EDGES.forEach(([from, to]) => addAdj(from, to, `${from}>${to}`))

    function clearHover() {
      viewport.classList.remove('wf-hovering')
      world.querySelectorAll('.wf-active').forEach((el) => el.classList.remove('wf-active'))
      svg.querySelectorAll('.wf-active').forEach((el) => el.classList.remove('wf-active'))
    }
    function applyHover(nodeId) {
      viewport.classList.add('wf-hovering')
      byId[nodeId]?.el?.classList.add('wf-active')
      const info = adjacency[nodeId]
      if (!info) return
      info.nodes.forEach((nid) => byId[nid]?.el?.classList.add('wf-active'))
      info.edges.forEach((ek) => {
        svg.querySelectorAll(`[data-edge="${CSS.escape(ek)}"]`).forEach((el) => el.classList.add('wf-active'))
      })
    }

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
      el.className = 'node wf-enter' + (n.shape === 'circle' ? ' sub-node' : '')
      el.style.left = n.x + 'px'
      el.style.top = n.y + 'px'
      el.style.setProperty('--glow', p.color + '55')
      // Entrance cascades left-to-right across the whole 2320px canvas, so
      // the pipeline visibly "boots up" phase by phase on first paint.
      el.style.animationDelay = `${(n.x / 2000) * 0.9}s`
      el.dataset.id = n.id
      el.dataset.status = n.status

      const badgeHtml = n.status === 'todo'
        ? '<span class="status-dot st-todo"></span>'
        : `<span class="status-dot st-${n.status}">${STATUS_GLYPH[n.status] || ''}</span>`

      el.innerHTML =
        `<div class="icon-badge${n.manual ? ' manual' : ''}" style="background:${p.color}">` +
          `<svg class="glyph" viewBox="0 0 24 24"><use href="#${p.icon}"></use></svg>` +
          badgeHtml +
        '</div>' +
        `<div class="node-label">${n.label}<span class="node-sub">${p.label}</span></div>`

      el.addEventListener('mouseenter', () => applyHover(n.id))
      el.addEventListener('mouseleave', clearHover)

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

    // Packet dots are driven by a plain requestAnimationFrame loop below
    // instead of SVG's native <animateMotion> -- SMIL depends on browser/OS
    // animation settings and xlink parsing quirks that are impossible to
    // verify without a live browser, and it was silently producing nothing.
    // Reading a path's own geometry with getPointAtLength() has none of
    // that: it's plain JS, always runs, always visible.
    const SVG_NS = 'http://www.w3.org/2000/svg'
    let packets = []

    function drawEdges() {
      const parts = [
        '<defs><marker id="wf-arrow" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
        '<path class="wf-arrow-fill" d="M0,0 L8,4 L0,8 Z" fill="#4A5C6C"></path></marker></defs>',
      ]
      const packetSpecs = []

      MAIN_EDGES.forEach((e, i) => {
        const a = byId[e[0]], b = byId[e[1]], label = e[2]
        const vertical = e[3] === 'vertical'
        const edgeKey = `${e[0]}>${e[1]}`
        let p1, p2, d
        if (vertical) {
          // Exit/enter top or bottom (whichever faces the other node) so the
          // curve clears the row it's leaving instead of cutting across it.
          const aDown = b.y >= a.y
          p1 = aDown ? centerBottom(a) : centerTop(a)
          p2 = aDown ? centerTop(b) : centerBottom(b)
          const bend = Math.max(40, Math.abs(p2.y - p1.y) * 0.5)
          d = `M ${p1.x} ${p1.y} C ${p1.x} ${p1.y + (aDown ? bend : -bend)}, ${p2.x} ${p2.y + (aDown ? -bend : bend)}, ${p2.x} ${p2.y}`
        } else {
          p1 = centerRight(a)
          p2 = centerLeft(b)
          const bend = Math.max(50, Math.abs(p2.x - p1.x) * 0.5)
          d = `M ${p1.x} ${p1.y} C ${p1.x + bend} ${p1.y}, ${p2.x - bend} ${p2.y}, ${p2.x} ${p2.y}`
        }
        const pathId = `mp-${e[0]}-${e[1]}-${i}`
        const color = PHASES[b.phase].color
        parts.push(`<path id="${pathId}" class="main" data-edge="${edgeKey}" d="${d}"></path>`)
        // Flow overlay -- same geometry, animated dash sweep in the brand
        // color of the phase it's feeding, so every connector visibly
        // carries motion toward its destination, not just the arrowhead.
        const flowDur = (2.6 + (i % 3) * 0.5).toFixed(2)
        parts.push(
          `<path class="main-flow" data-edge="${edgeKey}" d="${d}" style="stroke:${color};color:${color};animation-duration:${flowDur}s"></path>`
        )

        const dur = 2.2, stagger = (i % 4) * 0.5
        ;[0, dur / 2].forEach((begin) => {
          packetSpecs.push({ pathId, edgeKey, color, dur, offset: begin + stagger })
        })

        if (label) {
          const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2 - 7
          parts.push(`<text data-edge="${edgeKey}" x="${mx}" y="${my}" text-anchor="middle">${label}</text>`)
        }
      })

      SUB_EDGES.forEach((e) => {
        const a = byId[e[0]], b = byId[e[1]], label = e[2]
        const edgeKey = `${e[0]}>${e[1]}`
        const p1 = centerBottom(a), p2 = centerTop(b)
        const midY = (p1.y + p2.y) / 2
        const d = `M ${p1.x} ${p1.y} C ${p1.x} ${midY}, ${p2.x} ${midY}, ${p2.x} ${p2.y}`
        parts.push(`<path class="sub" data-edge="${edgeKey}" d="${d}"></path>`)
        parts.push(
          `<path class="sub-flow" data-edge="${edgeKey}" d="${d}" style="stroke:${PHASES[b.phase].color};color:${PHASES[b.phase].color}"></path>`
        )
        if (label) {
          parts.push(`<text data-edge="${edgeKey}" x="${(p1.x + p2.x) / 2}" y="${midY + 4}" text-anchor="middle">${label}</text>`)
        }
      })

      svg.innerHTML = parts.join('')

      // Packet circles live outside the innerHTML string above (SMIL is
      // gone) so they're created here, once the paths they ride are
      // actually in the DOM and have real geometry to query. Each packet
      // gets two trailing dots sampled from slightly earlier progress, so
      // it reads as a comet with a fading tail instead of a bare dot.
      packets = packetSpecs.map((spec) => {
        const pathEl = svg.querySelector(`#${spec.pathId}`)
        const dots = [1, 0.55, 0.22].map((opacity, idx) => {
          const circle = document.createElementNS(SVG_NS, 'circle')
          circle.setAttribute('r', String(3 - idx * 0.8))
          circle.setAttribute('class', 'packet')
          circle.dataset.edge = spec.edgeKey
          circle.style.fill = spec.color
          circle.style.color = spec.color
          circle.style.opacity = String(opacity)
          svg.appendChild(circle)
          return circle
        })
        return { dots, pathEl, dur: spec.dur, offset: spec.offset }
      })
    }

    drawEdges()

    // ---- packet animation loop (plain JS, not SMIL) ----
    let rafId
    const startTime = performance.now()
    function tick(now) {
      if (!animOnRef.current) {
        rafId = requestAnimationFrame(tick)
        return
      }
      const elapsed = (now - startTime) / 1000
      for (const p of packets) {
        if (!p.pathEl) continue
        const total = p.pathEl.getTotalLength()
        if (!total) continue
        const t = (((elapsed + p.offset) % p.dur) / p.dur + 1) % 1

        // Trailing dots sample progressively earlier progress along the
        // same path -- a cheap comet tail with no per-frame history buffer.
        p.dots.forEach((dot, idx) => {
          const dt = ((t - idx * 0.02) % 1 + 1) % 1
          const pt = p.pathEl.getPointAtLength(dt * total)
          dot.setAttribute('cx', pt.x)
          dot.setAttribute('cy', pt.y)
        })
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)

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

    // Wheel/drag zoom stays instant (a transition there would feel laggy
    // mid-gesture); a button click is a one-shot action, so it gets a brief
    // eased transition to feel like a deliberate camera move instead of a
    // snap-cut.
    function withEasedZoom(fn) {
      world.style.transition = 'transform 340ms cubic-bezier(.2,.8,.2,1)'
      fn()
      setTimeout(() => { world.style.transition = '' }, 360)
    }
    function onZoomIn() {
      const r = viewport.getBoundingClientRect()
      withEasedZoom(() => zoomAt(r.width / 2, r.height / 2, scale + 0.15))
    }
    function onZoomOut() {
      const r = viewport.getBoundingClientRect()
      withEasedZoom(() => zoomAt(r.width / 2, r.height / 2, scale - 0.15))
    }
    function onZoomReset() {
      // Fits the actual 2320x620 canvas to whatever the viewport measures
      // right now, instead of a hardcoded scale -- correct on both a phone
      // and a wide desktop monitor.
      const rect = viewport.getBoundingClientRect()
      const worldW = 2320, worldH = 620
      withEasedZoom(() => {
        scale = clamp(Math.min(rect.width / (worldW + 80), rect.height / (worldH + 80)), 0.32, 1.9)
        panX = (rect.width - worldW * scale) / 2
        panY = (rect.height - worldH * scale) / 2
        applyTransform()
      })
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

    // Cleanup: React StrictMode double-invokes this effect in dev (mount,
    // cleanup, mount again) specifically to catch bugs like the one that
    // used to be here -- `world.innerHTML = ''` doesn't just clear the
    // .node divs we created, it also deletes the real <svg ref={svgRef}>
    // element, since it's a React-rendered child of world. svgRef.current
    // never gets reassigned after that (no re-render triggers it), so the
    // second effect run kept drawing edges into a detached, invisible SVG
    // while the live `world` was left with no SVG at all -- which is
    // exactly why the connecting lines silently stopped appearing. Fix:
    // remove only what we imperatively created, and remove every listener
    // we attached (five of them were never being removed at all).
    return () => {
      cancelAnimationFrame(rafId)
      viewport.removeEventListener('wheel', onWheel)
      viewport.removeEventListener('pointerdown', onViewportPointerDown)
      world.removeEventListener('pointerdown', onWorldPointerDown)
      document.removeEventListener('pointermove', onDocumentPointerMove)
      document.removeEventListener('pointerup', onDocumentPointerUp)
      zoomInRef.current?.removeEventListener('click', onZoomIn)
      zoomOutRef.current?.removeEventListener('click', onZoomOut)
      zoomResetRef.current?.removeEventListener('click', onZoomReset)
      world.querySelectorAll('.node').forEach((el) => el.remove())
      svg.innerHTML = ''
      phaseLegendEl.innerHTML = ''
    }
  }, [])

  return (
    <div className="workflow-page" ref={pageRef}>
      <div className="wrap">
        <header>
          <p className="eyebrow">Nexaris · AI Outreach Agent</p>
          <h1>Agent Workflow</h1>
        </header>

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
          <div className="view-controls">
            <button
              type="button"
              className={`toggle-chip${solid ? ' active' : ''}`}
              aria-pressed={solid}
              onClick={toggleSolid}
            >
              Solid background
            </button>
            <button
              type="button"
              className={`toggle-chip${animOn ? ' active' : ''}`}
              aria-pressed={animOn}
              onClick={toggleAnimations}
            >
              Animations {animOn ? 'on' : 'off'}
            </button>
            <div className="zoom-controls">
              <button ref={zoomOutRef} aria-label="Zoom out" type="button">&minus;</button>
              <button ref={zoomResetRef} aria-label="Reset view" type="button">&#9678;</button>
              <button ref={zoomInRef} aria-label="Zoom in" type="button">+</button>
            </div>
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
          <div className="rule-chip"><b>Hard gate:</b> nothing crosses the approval node without Mohamad clearing it — no bypass exists in the design.</div>
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
