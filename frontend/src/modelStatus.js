export function modelStatus(r) {
  if (r.state === 'running' || (r.success == null && r.state !== 'failed')) return 'Running'
  if (r.success === false || r.state === 'failed') {
    const timedOut = r.error_category === 'timeout' || /timed?\s*out|timeout/i.test(r.error || '')
    return timedOut ? 'Timed out' : 'Failed'
  }
  return r.text?.trim() ? 'Completed' : 'Incomplete'
}
