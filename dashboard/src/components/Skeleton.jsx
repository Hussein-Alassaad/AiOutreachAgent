/** Shimmering placeholder used everywhere a plain "Loading…" text used to be. */
export function Skeleton({ className = '' }) {
  return <div className={`skeleton rounded-lg ${className}`} />
}

export function SkeletonCard() {
  return (
    <div className="glass rounded-2xl p-4">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="mt-2 h-3 w-1/4" />
      <Skeleton className="mt-4 h-3 w-full" />
      <Skeleton className="mt-1.5 h-3 w-5/6" />
    </div>
  )
}
