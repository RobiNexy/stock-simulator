export type Bar = {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  /** 周期内交易日数，仅周/月聚合设置。 */
  n_days?: number
  /** 当前未完成周期，仅最后一根周/月K可能为 true。 */
  provisional?: boolean
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

export type EmaSeed = 'sma' | 'first'

export type MacdOptions = {
  /** 指标使用的价格序列，默认收盘价。 */
  price?: 'close' | 'log'
  /** EMA 种子，默认 SMA。 */
  seed?: EmaSeed
  /** 柱体倍数，国内常用 2。 */
  histMult?: number
}

function weekEndKey(date: string): string {
  const value = new Date(`${date}T00:00:00Z`)
  const daysToFriday = (5 - value.getUTCDay() + 7) % 7
  value.setUTCDate(value.getUTCDate() + daysToFriday)
  return value.toISOString().slice(0, 10)
}

function isProvisional(key: string, lastDate: string, timeframe: Timeframe): boolean {
  if (timeframe === 'week') return lastDate < key
  if (timeframe === 'month') {
    const [year, month] = key.split('-').map(Number)
    const monthEnd = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10)
    return lastDate < monthEnd
  }
  return false
}

export function aggregateBars(bars: Bar[], timeframe: Timeframe): Bar[] {
  if (timeframe === 'day') return bars
  const groups = new Map<string, { key: string; items: Bar[] }>()
  for (const bar of bars) {
    const key = timeframe === 'week' ? weekEndKey(bar.date) : bar.date.slice(0, 7)
    const group = groups.get(key)
    if (group) group.items.push(bar)
    else groups.set(key, { key, items: [bar] })
  }
  const entries = [...groups.values()]
  return entries.map((entry, index) => {
    const group = entry.items
    const last = group[group.length - 1]
    return {
      date: last.date,
      open: group[0].open,
      high: Math.max(...group.map((bar) => bar.high)),
      low: Math.min(...group.map((bar) => bar.low)),
      close: last.close,
      volume: group.reduce((sum, bar) => sum + bar.volume, 0),
      amount: group.reduce((sum, bar) => sum + bar.amount, 0),
      n_days: group.length,
      provisional: index === entries.length - 1 && isProvisional(entry.key, last.date, timeframe),
    }
  })
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

export function exponentialMovingAverage(
  values: number[],
  period: number,
  seed: EmaSeed = 'sma',
): number[] {
  if (!Number.isInteger(period) || period < 1 || values.length === 0) return []
  const output = new Array<number>(values.length).fill(Number.NaN)
  if (values.length < period) return output

  const alpha = 2 / (period + 1)
  let start: number
  if (seed === 'sma') {
    let sum = 0
    for (let index = 0; index < period; index += 1) sum += values[index]
    output[period - 1] = sum / period
    start = period
  } else {
    output[0] = values[0]
    start = 1
  }
  for (let index = start; index < values.length; index += 1) {
    output[index] = alpha * values[index] + (1 - alpha) * output[index - 1]
  }
  return output
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

export function calculateMacd(
  bars: Bar[],
  parameters: MacdParams,
  options: MacdOptions = {},
): MacdPoint[] {
  if (!macdParamsValid(parameters) || bars.length === 0) return []
  const { fast, slow, signal } = parameters
  const { price = 'close', seed = 'sma', histMult = 2 } = options
  const values = bars.map((bar) => (price === 'log' ? Math.log(bar.close) : bar.close))
  const fastEma = exponentialMovingAverage(values, fast, seed)
  const slowEma = exponentialMovingAverage(values, slow, seed)
  const dif = fastEma.map((value, index) => value - slowEma[index])
  const dea = new Array<number>(bars.length).fill(Number.NaN)

  const firstValid = dif.findIndex((value) => Number.isFinite(value))
  if (firstValid >= 0) {
    const deaValues = exponentialMovingAverage(dif.slice(firstValid), signal, seed)
    for (let index = 0; index < deaValues.length; index += 1) {
      dea[firstValid + index] = deaValues[index]
    }
  }

  return bars.map((bar, index) => ({
    date: bar.date,
    dif: dif[index],
    dea: dea[index],
    histogram: Number.isFinite(dif[index]) && Number.isFinite(dea[index])
      ? histMult * (dif[index] - dea[index])
      : Number.NaN,
  }))
}
