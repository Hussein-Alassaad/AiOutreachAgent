import { useEffect, useRef } from 'react'
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'

/**
 * Counts up (or down) to `value` whenever it changes -- used on every KPI
 * tile so numbers feel alive instead of snapping in, and so a realtime
 * update (a new lead landing) is visually obvious rather than a silent
 * DOM swap.
 */
export default function AnimatedNumber({ value = 0, decimals = 0 }) {
  const motionValue = useMotionValue(0)
  const spring = useSpring(motionValue, { stiffness: 120, damping: 20, mass: 0.6 })
  const rounded = useTransform(spring, (v) => v.toFixed(decimals))
  const first = useRef(true)

  useEffect(() => {
    motionValue.set(value)
    first.current = false
  }, [value]) // eslint-disable-line react-hooks/exhaustive-deps

  return <motion.span>{rounded}</motion.span>
}
