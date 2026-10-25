// Persisted timestamps are UTC. Legacy records were written without a
// timezone suffix, so treat suffix-less values as UTC (not local) before
// formatting in the user's timezone.
export function toDate(iso) {
  if (!iso) return null
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)
  const date = new Date(hasZone ? iso : `${iso}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatTimestamp(iso) {
  const date = toDate(iso)
  if (!date) return ''
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
