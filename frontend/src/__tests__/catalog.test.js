import { describe, it, expect, beforeEach, vi } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  vi.unstubAllGlobals()
})

describe('model catalog fetch', () => {
  it('uses the forced-refresh endpoint when force is requested', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ models: [{ id: 'free/model:free' }] }),
    })
    vi.stubGlobal('fetch', fetchMock)

    const { exposeModelCatalog } = await import('../catalog.js')
    await exposeModelCatalog(true)

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8001/api/models/refresh',
      { method: 'POST' },
    )
  })

  it('uses GET for normal loads and propagates failures', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500 })
    vi.stubGlobal('fetch', fetchMock)

    const { exposeModelCatalog } = await import('../catalog.js')
    await expect(exposeModelCatalog()).rejects.toThrow('500')

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8001/api/models',
      { method: 'GET' },
    )
  })
})
