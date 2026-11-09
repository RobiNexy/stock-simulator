export type Bar = {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
}

export type Timeframe = 'day' | 'week' | 'month'

export type LinePoint = {
  date: string
  value: number
}

export type MacdPoint = {
  date: string
  dif: number
  dea: number
  histogram: number
}

export type MacdParams = {
  fast: number
  slow: number
  signal: number
}

export function aggregateBars(bars: Bar[], timeframe: Timeframe): Bar[] {
  if (timeframe === 'day') return bars
  const groups = new Map<string, Bar[]>()
  for (const bar of bars) {
    const date = new Date(`${bar.date}T00:00:00Z`)
    let key: string
    if (timeframe === 'week') {
      const daysFromMonday = (date.getUTCDay() + 6) % 7
      date.setUTCDate(date.getUTCDate() - daysFromMonday)
      key = date.toISOString().slice(0, 10)
    } else {
      key = `${bar.date.slice(0, 7)}-01`
    }
    const group = groups.get(key)
    if (group) group.push(bar)
    else groups.set(key, [bar])
  }
  return [...groups.entries()].map(([date, group]) => ({
    date,
    open: group[0].open,
    high: Math.max(...group.map((bar) => bar.high)),
    low: Math.min(...group.map((bar) => bar.low)),
    close: group[group.length - 1].close,
    volume: group.reduce((sum, bar) => sum + bar.volume, 0),
    amount: group.reduce((sum, bar) => sum + bar.amount, 0),
  }))
}

export type LogicalRange = {
  from: number
  to: number
}

export function clampRangeIndex(index: number, length: number): number {
  if (length === 0) return 0
  return Math.max(0, Math.min(length - 1, index))
}

export function indexForDate(bars: Bar[], target: string, preferAfter: boolean): number {
  const index = bars.findIndex((bar) => bar.date >= target)
  if (index < 0) return bars.length - 1
  if (!preferAfter && bars[index].date > target && index > 0) return index - 1
  return index
}

export function preserveVisibleRange(
  oldRange: LogicalRange | null,
  oldBars: Bar[],
  newBars: Bar[],
): LogicalRange | null {
  if (!oldRange || oldBars.length === 0 || newBars.length === 0) return null

  const followsLatest = oldRange.to >= oldBars.length - 1.5
  if (followsLatest) {
    const span = oldRange.to - oldRange.from
    const rightOffset = oldRange.to - (oldBars.length - 1)
    const to = newBars.length - 1 + rightOffset
    return { from: to - span, to }
  }

  const oldFromIndex = clampRangeIndex(Math.floor(oldRange.from), oldBars.length)
  const oldToIndex = clampRangeIndex(Math.ceil(oldRange.to), oldBars.length)
  const fromDate = oldBars[oldFromIndex].date
  const toDate = oldBars[oldToIndex].date
  const fromFraction = oldRange.from - Math.floor(oldRange.from)
  const toFraction = oldRange.to - Math.ceil(oldRange.to)

  const fromIndex = indexForDate(newBars, fromDate, false)
  const toIndex = indexForDate(newBars, toDate, true)
  const from = fromIndex + fromFraction
  const to = toIndex + toFraction
  return { from, to: Math.max(from + 1, to) }
}

export function defaultVisibleRange(length: number, barsToShow = 80): LogicalRange {
  return { from: Math.max(0, length - barsToShow), to: length + 2 }
}

export function simpleMovingAverage(bars: Bar[], period: number): LinePoint[] {
  if (!Number.isInteger(period) || period < 1) return []
  let sum = 0
  return bars.flatMap((bar, index) => {
    sum += bar.close
    if (index >= period) sum -= bars[index - period].close
    if (index < period - 1) return []
    return [{ date: bar.date, value: sum / period }]
  })
}

export function exponentialMovingAverage(values: number[], period: number): number[] {
  const alpha = 2 / (period + 1)
  return values.reduce<number[]>((ema, value, index) => {
    ema.push(index === 0 ? value : alpha * value + (1 - alpha) * ema[index - 1])
    return ema
  }, [])
}

export function macdParamsValid({ fast, slow, signal }: MacdParams): boolean {
  return (
    Number.isInteger(fast) &&
    Number.isInteger(slow) &&
    Number.isInteger(signal) &&
    fast > 0 &&
    fast < slow &&
    signal > 1
  )
}

export function calculateMacd(bars: Bar[], parameters: MacdParams): MacdPoint[] {
  if (!macdParamsValid(parameters) || bars.length === 0) return []
  const { fast, slow, signal } = parameters
  const logClose = bars.map((bar) => Math.log(bar.close))
  const fastEma = exponentialMovingAverage(logClose, fast)
  const slowEma = exponentialMovingAverage(logClose, slow)
  const dif = fastEma.map((value, index) => value - slowEma[index])
  const dea = exponentialMovingAverage(dif, signal)
  return bars.map((bar, index) => ({
    date: bar.date,
    dif: dif[index],
    dea: dea[index],
    histogram: dif[index] - dea[index],
  }))
}
