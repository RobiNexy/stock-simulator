import assert from 'node:assert/strict'
import {
  aggregateBars,
  calculateMacd,
  defaultVisibleRange,
  macdParamsValid,
  preserveVisibleRange,
  simpleMovingAverage,
  type Bar,
} from '../src/chartMath.ts'

const bar = (date: string, close: number, volume = 100): Bar => ({
  date,
  open: close,
  high: close + 1,
  low: close - 1,
  close,
  volume,
  amount: close * volume,
})

const daily = [
  bar('2024-01-01', 10),
  bar('2024-01-02', 11),
  bar('2024-01-03', 12),
  bar('2024-01-04', 13),
  bar('2024-01-05', 14),
  bar('2024-01-08', 15),
]

const weekly = aggregateBars(daily, 'week')
assert.equal(weekly.length, 2)
assert.equal(weekly[0].date, '2024-01-01')
assert.equal(weekly[0].open, 10)
assert.equal(weekly[0].close, 14)
assert.equal(weekly[0].high, 15)
assert.equal(weekly[0].low, 9)
assert.equal(weekly[0].volume, 500)
assert.equal(weekly[1].date, '2024-01-08')

const monthly = aggregateBars([...daily, bar('2024-02-01', 20)], 'month')
assert.equal(monthly.length, 2)
assert.equal(monthly[0].close, 15)
assert.equal(monthly[1].open, 20)

const sma = simpleMovingAverage(daily, 3)
assert.equal(sma.length, 4)
assert.equal(sma[0].date, '2024-01-03')
assert.equal(sma[0].value, 11)
assert.equal(sma[3].value, 14)
assert.deepEqual(simpleMovingAverage(daily, 0), [])
assert.deepEqual(simpleMovingAverage(daily, 1), daily.map((item) => ({ date: item.date, value: item.close })))

assert.equal(macdParamsValid({ fast: 10, slow: 22, signal: 8 }), true)
assert.equal(macdParamsValid({ fast: 22, slow: 10, signal: 8 }), false)
assert.equal(macdParamsValid({ fast: 10, slow: 22, signal: 1 }), false)
assert.deepEqual(calculateMacd(daily, { fast: 22, slow: 10, signal: 8 }), [])

const macd = calculateMacd(daily, { fast: 3, slow: 5, signal: 2 })
assert.equal(macd.length, daily.length)
assert.equal(macd[0].dif, 0)
assert.equal(macd[0].dea, 0)
assert.equal(macd[0].histogram, 0)
assert.ok(macd[5].dif > 0)

const longBars: Bar[] = Array.from({ length: 100 }, (_, index) =>
  bar(`2024-${String(1 + Math.floor(index / 28)).padStart(2, '0')}-${String((index % 28) + 1).padStart(2, '0')}`, 10 + index),
)

assert.deepEqual(defaultVisibleRange(500), { from: 420, to: 502 })
assert.deepEqual(defaultVisibleRange(30), { from: 0, to: 32 })

const zoomedRange = { from: 10.5, to: 40.25 }
const afterAppend = preserveVisibleRange(zoomedRange, longBars, [...longBars, bar('2024-05-01', 200)])
assert.deepEqual(afterAppend, zoomedRange)

const followingRange = { from: 90, to: 102 }
const followingAfterAppend = preserveVisibleRange(followingRange, longBars, [...longBars, bar('2024-05-01', 200)])
assert.deepEqual(followingAfterAppend, { from: 91, to: 103 })

const weeklyBars = aggregateBars(longBars, 'week')
const weeklyRange = preserveVisibleRange(zoomedRange, longBars, weeklyBars)
assert.ok(weeklyRange)
const fromDate = longBars[Math.floor(zoomedRange.from)].date
const weekIndex = Math.floor(weeklyRange.from)
assert.ok(weeklyBars[weekIndex].date <= fromDate)
assert.ok(weekIndex + 1 >= weeklyBars.length || weeklyBars[weekIndex + 1].date > fromDate)
assert.ok(weeklyRange.from < weeklyRange.to)

assert.equal(preserveVisibleRange(null, longBars, weeklyBars), null)
assert.equal(preserveVisibleRange(zoomedRange, [], weeklyBars), null)

console.log('chart math checks passed')