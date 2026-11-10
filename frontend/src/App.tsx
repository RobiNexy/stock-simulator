import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineStyle,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts'
import {
  aggregateBars,
  calculateMacd,
  defaultVisibleRange,
  macdParamsValid,
  preserveVisibleRange,
  simpleMovingAverage,
  type Bar,
  type MacdOptions,
  type MacdPoint,
  type MacdParams,
  type Timeframe,
} from './chartMath.ts'

type Portfolio = {
  cash: number
  quantity: number
  available_to_sell: number
  average_cost: number
  market_value: number
  unrealized_pnl: number
  total_asset: number
}

type Trade = {
  date: string
  side: 'buy' | 'sell'
  quantity: number
  price: number
  fees: number
  status: 'filled' | 'rejected'
  reason?: string
}

type SimState = {
  active: boolean
  initial_cash?: number
  asset_type: 'stock' | 'custom_index' | null
  asset_id: string | null
  current_date: string | null
  start_date?: string | null
  adjust: 'none' | 'forward'
  price_mode: 'open' | 'close' | 'mid' | 'random'
  bars: Bar[]
  current_bar: Bar | null
  pending_order: { side: 'buy' | 'sell'; quantity: number } | null
  portfolio?: Portfolio
  recent_trades?: Trade[]
  fee_settings?: {
    commission_rate: number
    minimum_commission: number
    stamp_duty_rate: number
    transfer_fee_rate: number
  }
}

type CustomIndex = {
  id: string
  name: string
  base_date: string
  components: { code: string; weight: number }[]
}

type AuthUser = {
  id: string
  username: string
  is_admin: boolean
}

type MaSetting = {
  period: number
  visible: boolean
}

const MA_DEFAULTS: Record<Timeframe, number[]> = {
  // 一月按 21 个交易日（用户 2026-11-09 裁决）。
  day: [5, 21, 61, 122, 244],
  week: [4, 13, 26, 52],
  month: [3, 6, 12, 24],
}

const MACD_DEFAULTS: Record<Timeframe, MacdParams> = {
  day: { fast: 10, slow: 22, signal: 8 },
  week: { fast: 12, slow: 26, signal: 9 },
  month: { fast: 12, slow: 26, signal: 9 },
}

const MACD_OPTIONS: Record<Timeframe, MacdOptions> = {
  // 日线保持已实现的 log(close)、first 种子和 1 倍柱体。
  day: { price: 'log', seed: 'first', histMult: 1 },
  // 周/月按新规格：收盘价、SMA 种子、国内 2 倍柱体。
  week: { price: 'close', seed: 'sma', histMult: 2 },
  month: { price: 'close', seed: 'sma', histMult: 2 },
}

const MA_COLORS = ['#6C4EE3', '#1D7FBF', '#B04FC4', '#3F4E68', '#7F93B3']
const MA_WIDTHS = [3, 2, 2, 2, 2] as const
const MA_STYLES = [
  LineStyle.Solid,
  LineStyle.Dotted,
  LineStyle.Dashed,
  LineStyle.LargeDashed,
  LineStyle.SparseDotted,
]
const MACD_COLORS = { dif: '#6C4EE3', dea: '#1D7FBF' }

const MACD_PRESETS: { id: string; label: string; params: MacdParams }[] = [
  { id: 'default', label: '默认 10 / 22 / 8', params: { fast: 10, slow: 22, signal: 8 } },
  { id: 'theory', label: '理论较优 5 / 10 / 9', params: { fast: 5, slow: 10, signal: 9 } },
  { id: 'classic', label: '经典 12 / 26 / 9', params: { fast: 12, slow: 26, signal: 9 } },
]

const EMPTY_STATE: SimState = {
  active: false,
  asset_type: null,
  asset_id: null,
  current_date: null,
  adjust: 'forward',
  price_mode: 'open',
  bars: [],
  current_bar: null,
  pending_order: null,
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const payload: unknown = response.status === 204 ? undefined : await response.json()
  if (!response.ok) {
    if (response.status === 401) {
      window.dispatchEvent(new Event('auth-expired'))
    }
    const detail =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`
    throw new Error(detail)
  }
  return payload as T
}

function money(value: number | undefined): string {
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value ?? 0)
}

function compactNumber(value: number | undefined): string {
  return new Intl.NumberFormat('zh-CN', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value ?? 0)
}

function KlineChart({
  bars,
  comparison,
  maSettings,
  macdPoints,
  macdPreheat,
  showCandles,
  timeframe,
  resetKey,
}: {
  bars: Bar[]
  comparison: Bar[]
  maSettings: MaSetting[]
  macdPoints: MacdPoint[]
  macdPreheat: number
  showCandles: boolean
  timeframe: Timeframe
  resetKey: string
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const comparisonRef = useRef<ISeriesApi<'Line'> | null>(null)
  const maRefs = useRef<ISeriesApi<'Line'>[]>([])
  const macdHistogramRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const macdDifRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdDeaRef = useRef<ISeriesApi<'Line'> | null>(null)
  const previousBarsRef = useRef<Bar[]>([])
  const resetKeyRef = useRef(resetKey)
  const [hover, setHover] = useState<{ date: string; x: number; y: number } | null>(null)

  useEffect(() => {
    if (resetKeyRef.current !== resetKey) {
      resetKeyRef.current = resetKey
      previousBarsRef.current = []
    }
  }, [resetKey])
  const comparisonBars = useMemo(
    () => aggregateBars(comparison, timeframe),
    [comparison, timeframe],
  )

  useEffect(() => {
    if (!hostRef.current) return
    const chart = createChart(hostRef.current, {
      autoSize: true,
      height: 600,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#746583',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.24 } },
      timeScale: { borderVisible: false, rightOffset: 2, barSpacing: 9 },
      crosshair: {
        vertLine: { color: 'rgba(165, 136, 210, .32)', labelBackgroundColor: '#9c7be0' },
        horzLine: { color: 'rgba(165, 136, 210, .32)', labelBackgroundColor: '#9c7be0' },
      },
    })
    candleRef.current = chart.addSeries(CandlestickSeries, {
      upColor: '#ef7896',
      downColor: '#62c6a6',
      borderUpColor: '#e86588',
      borderDownColor: '#4bb892',
      wickUpColor: '#ee8ba2',
      wickDownColor: '#78cdb2',
      borderVisible: true,
      wickVisible: true,
      priceLineVisible: false,
      visible: true,
    })
    maRefs.current = MA_COLORS.map((color, index) => chart.addSeries(LineSeries, {
      color,
      lineWidth: MA_WIDTHS[index],
      lineStyle: MA_STYLES[index],
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    }))
    volumeRef.current = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
        lastValueVisible: false,
        priceLineVisible: false,
      },
      1,
    )
    macdHistogramRef.current = chart.addSeries(
      HistogramSeries,
      {
        priceScaleId: 'macd',
        lastValueVisible: false,
        priceLineVisible: false,
      },
      2,
    )
    macdDifRef.current = chart.addSeries(LineSeries, {
      color: '#8E7CD8',
      lineWidth: 2,
      priceScaleId: 'macd',
      priceLineVisible: false,
      lastValueVisible: false,
    }, 2)
    macdDeaRef.current = chart.addSeries(LineSeries, {
      color: '#5FA8C9',
      lineWidth: 2,
      priceScaleId: 'macd',
      priceLineVisible: false,
      lastValueVisible: false,
    }, 2)
    comparisonRef.current = chart.addSeries(LineSeries, {
      color: '#a17bc9',
      lineWidth: 2,
      lineStyle: 0,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 3,
    })
    chart.panes()[1]?.setHeight(92)
    chart.panes()[2]?.setHeight(150)
    volumeRef.current?.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0 } })
    macdHistogramRef.current?.priceScale().applyOptions({ scaleMargins: { top: 0.12, bottom: 0.12 } })
    chartRef.current = chart

    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param.point || param.time === undefined || typeof param.time !== 'string') {
        setHover(null)
        return
      }
      const width = hostRef.current?.clientWidth ?? 0
      const height = hostRef.current?.clientHeight ?? 0
      const tooltipHeight = 96
      const below = param.point.y + 14
      setHover({
        date: param.time,
        x: Math.max(8, Math.min(param.point.x + 14, Math.max(8, width - 214))),
        y: below + tooltipHeight > height
          ? Math.max(8, param.point.y - tooltipHeight - 14)
          : Math.max(8, below),
      })
    }
    chart.subscribeCrosshairMove(handleCrosshairMove)

    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      volumeRef.current = null
      comparisonRef.current = null
      maRefs.current = []
      macdHistogramRef.current = null
      macdDifRef.current = null
      macdDeaRef.current = null
      previousBarsRef.current = []
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const oldBars = previousBarsRef.current
    const oldRange = chart.timeScale().getVisibleLogicalRange()
    candleRef.current?.applyOptions({ visible: showCandles })
    candleRef.current?.setData(
      bars.map((bar) => {
        const data = {
          time: bar.date as Time,
          open: bar.open,
          high: bar.high,
          low: bar.low,
          close: bar.close,
        }
        if (bar.virtual) {
          return {
            ...data,
            color: 'rgba(167, 139, 232, .3)',
            borderColor: 'rgba(167, 139, 232, .62)',
            wickColor: 'rgba(167, 139, 232, .5)',
          }
        }
        if (!bar.provisional) return data
        const rising = bar.close >= bar.open
        return {
          ...data,
          color: rising ? 'rgba(239, 120, 150, .5)' : 'rgba(98, 198, 166, .5)',
          borderColor: rising ? 'rgba(232, 101, 136, .55)' : 'rgba(75, 184, 146, .55)',
          wickColor: rising ? 'rgba(238, 139, 162, .55)' : 'rgba(120, 205, 178, .55)',
        }
      }),
    )
    maRefs.current.forEach((series, index) => {
      const setting = maSettings[index]
      if (!setting) {
        series.applyOptions({ visible: false })
        series.setData([])
        return
      }
      series.applyOptions({ visible: setting.visible })
      series.setData(
        setting.visible
          ? simpleMovingAverage(bars, setting.period).map((point) => ({
              time: point.date as Time,
              value: point.value,
            }))
          : [],
      )
    })
    volumeRef.current?.setData(
      bars.map((bar) => ({
        time: bar.date as Time,
        value: bar.volume,
        color: bar.close >= bar.open ? 'rgba(239, 120, 150, .35)' : 'rgba(98, 198, 166, .35)',
      })),
    )
    macdHistogramRef.current?.setData(
      macdPoints
        .filter((point) => Number.isFinite(point.histogram))
        .map((point) => ({
          time: point.date as Time,
          value: point.histogram,
          color: point.histogram >= 0 ? 'rgba(239, 120, 150, .62)' : 'rgba(98, 198, 166, .62)',
        })),
    )
    macdDifRef.current?.setData(macdPoints
      .filter((point) => Number.isFinite(point.dif))
      .map((point) => ({
        time: point.date as Time,
        value: point.dif,
      })))
    macdDeaRef.current?.setData(macdPoints
      .filter((point) => Number.isFinite(point.dea))
      .map((point) => ({
        time: point.date as Time,
        value: point.dea,
      })))
    const firstPrice = bars[0]?.close
    const firstComparison = comparisonBars[0]?.close
    if (firstPrice && firstComparison) {
      comparisonRef.current?.setData(
        comparisonBars.map((item) => ({
          time: item.date as Time,
          value: (item.close / firstComparison) * firstPrice,
        })),
      )
    } else {
      comparisonRef.current?.setData([])
    }
    const nextRange = preserveVisibleRange(oldRange, oldBars, bars)
    if (nextRange) {
      chart.timeScale().setVisibleLogicalRange(nextRange)
    } else {
      chart.timeScale().setVisibleLogicalRange(defaultVisibleRange(bars.length))
    }
    previousBarsRef.current = bars
  }, [bars, comparisonBars, macdPoints, maSettings, showCandles, timeframe])

  const hoverIndex = hover ? bars.findIndex((bar) => bar.date === hover.date) : -1
  const activeIndex = hoverIndex >= 0 ? hoverIndex : bars.length - 1
  const activeBar = bars[activeIndex]
  const activeMacd = macdPoints[activeIndex]
  const macdReady = Boolean(
    activeMacd
    && Number.isFinite(activeMacd.dif)
    && Number.isFinite(activeMacd.dea)
    && Number.isFinite(activeMacd.histogram),
  )
  const macdInPreheat = macdPreheat > 0 && activeIndex < macdPreheat
  const macdPreheatShort = macdPreheat > 0 && bars.length < macdPreheat
  const provisionalLabel = timeframe === 'week' ? '未完成周K' : timeframe === 'month' ? '未完成月K' : ''
  const maValues = maSettings.map((setting) => {
    if (!setting.visible || activeIndex < setting.period - 1) return null
    let sum = 0
    for (let index = activeIndex - setting.period + 1; index <= activeIndex; index += 1) {
      sum += bars[index].close
    }
    return sum / setting.period
  })

  return (
    <>
      <div className="chart-live-legend">
        <span className="live-date">{activeBar?.date ?? '—'}</span>
        {activeBar?.provisional && provisionalLabel && <span className="live-provisional">{provisionalLabel}</span>}
        {activeBar?.virtual && <span className="live-virtual">虚拟停牌K</span>}
        {!activeBar?.virtual && activeBar?.virtualDays ? (
          <span className="live-virtual">含 {activeBar.virtualDays} 个虚拟日</span>
        ) : null}
        {activeBar && (
          <>
            <span className="live-ohlc">
              开 <b>{activeBar.open.toFixed(2)}</b>
              高 <b>{activeBar.high.toFixed(2)}</b>
              低 <b>{activeBar.low.toFixed(2)}</b>
              收 <b className={activeBar.close >= activeBar.open ? 'up-text' : 'down-text'}>{activeBar.close.toFixed(2)}</b>
            </span>
            <span className="live-volume">量 <b>{compactNumber(activeBar.volume)}</b></span>
            {timeframe !== 'day' && activeBar.n_days ? (
              <span className="live-days">{activeBar.n_days} 个交易日</span>
            ) : null}
          </>
        )}
        <span className="legend-sep" />
        {maSettings.map((setting, index) => {
          if (!setting.visible) return null
          return (
            <span className="live-item" key={`ma-${setting.period}-${index}`}>
              <i
                className={`live-line ma-line-${index % 5}`}
                style={{
                  '--line-color': MA_COLORS[index % MA_COLORS.length],
                } as CSSProperties}
              />
              <span>MA{setting.period}</span>
              <b>{maValues[index] === null ? '—' : maValues[index]?.toFixed(2)}</b>
            </span>
          )
        })}
        <span className="legend-sep" />
        <span className="live-item">
          <i className="live-line ma-line-0" style={{ '--line-color': MACD_COLORS.dif } as CSSProperties} />
          <span>DIF</span><b>{macdReady ? activeMacd?.dif.toFixed(3) : '—'}</b>
        </span>
        <span className="live-item">
          <i className="live-line ma-line-0" style={{ '--line-color': MACD_COLORS.dea } as CSSProperties} />
          <span>DEA</span><b>{macdReady ? activeMacd?.dea.toFixed(3) : '—'}</b>
        </span>
        <span className="live-item">
          <i className="live-bar" />
          <span>MACD</span>
          <b className={macdReady ? (activeMacd!.histogram >= 0 ? 'up-text' : 'down-text') : ''}>
            {macdReady ? activeMacd?.histogram.toFixed(3) : '—'}
          </b>
        </span>
        {macdPreheatShort && <span className="live-preheat">数据不足预热</span>}
        {!macdPreheatShort && macdInPreheat && <span className="live-preheat">MACD 预热期</span>}
      </div>
      <div className="chart-stage">
        <div className="chart-canvas" ref={hostRef} aria-label="K 线图" />
        <span className="chart-pane-label volume-label">成交量</span>
        <span className="chart-pane-label macd-label">MACD</span>
        {hover && activeBar && (
          <div className="chart-tooltip" style={{ left: hover.x, top: hover.y }}>
            <strong>{activeBar.date}{activeBar.provisional && provisionalLabel ? ` · ${provisionalLabel}` : ''}</strong>
            <div className="tooltip-grid">
              <span>开 <b>{activeBar.open.toFixed(2)}</b></span>
              <span>高 <b>{activeBar.high.toFixed(2)}</b></span>
              <span>低 <b>{activeBar.low.toFixed(2)}</b></span>
              <span>收 <b className={activeBar.close >= activeBar.open ? 'up-text' : 'down-text'}>{activeBar.close.toFixed(2)}</b></span>
              <span>量 <b>{compactNumber(activeBar.volume)}</b></span>
              {timeframe !== 'day' && activeBar.n_days ? <span>{activeBar.n_days} 个交易日</span> : null}
              {activeBar.virtual && <span>虚拟停牌K</span>}
              {!activeBar.virtual && activeBar.virtualDays ? <span>含 {activeBar.virtualDays} 个虚拟日</span> : null}
              {macdInPreheat && <span>MACD 预热期</span>}
            </div>
          </div>
        )}
      </div>
    </>
  )
}

type TradingAppProps = {
  user: AuthUser
  onLogout: () => void
}

function TradingApp({ user, onLogout }: TradingAppProps) {
  const [sim, setSim] = useState<SimState>(EMPTY_STATE)
  const [timeframe, setTimeframe] = useState<Timeframe>('day')
  const [showCandles, setShowCandles] = useState(true)
  const [maSettings, setMaSettings] = useState<Record<Timeframe, MaSetting[]>>({
    day: MA_DEFAULTS.day.map((period) => ({ period, visible: true })),
    week: MA_DEFAULTS.week.map((period) => ({ period, visible: true })),
    month: MA_DEFAULTS.month.map((period) => ({ period, visible: true })),
  })
  const [macdParams, setMacdParams] = useState<Record<Timeframe, MacdParams>>(MACD_DEFAULTS)
  const [indices, setIndices] = useState<CustomIndex[]>([])
  const [assetKind, setAssetKind] = useState<'stock' | 'custom_index'>('stock')
  const [stockCode, setStockCode] = useState('600000')
  const [stockPool, setStockPool] = useState<'all' | 'hs300' | 'sz50' | 'zz500'>('all')
  const [selectedIndex, setSelectedIndex] = useState('')
  const [comparisonId, setComparisonId] = useState('')
  const [comparisonBars, setComparisonBars] = useState<Bar[]>([])
  const [startDate, setStartDate] = useState('2024-01-02')
  const [quantity, setQuantity] = useState(100)
  const [allocationPct, setAllocationPct] = useState<number | null>(null)
  const [indexName, setIndexName] = useState('自定义指数')
  const [indexCodes, setIndexCodes] = useState('600000\n000001')
  const [indexWeights, setIndexWeights] = useState('')
  const [seedCash, setSeedCash] = useState(1_000_000)
  const [commissionRate, setCommissionRate] = useState(0.00025)
  const [minimumCommission, setMinimumCommission] = useState(5)
  const [stampDutyRate, setStampDutyRate] = useState(0.0005)
  const [transferFeeRate, setTransferFeeRate] = useState(0.0001)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const chartBars = useMemo(() => aggregateBars(sim.bars, timeframe), [sim.bars, timeframe])
  const currentMaSettings = maSettings[timeframe]
  const currentMacdParams = macdParams[timeframe]
  const macdPoints = useMemo(
    () => calculateMacd(chartBars, currentMacdParams, MACD_OPTIONS[timeframe]),
    [chartBars, currentMacdParams, timeframe],
  )
  const macdValid = macdParamsValid(currentMacdParams)
  const macdPreheat = timeframe === 'day'
    ? 0
    : Math.ceil(Math.log(100) / (2 / (currentMacdParams.slow + 1)))
  const activeMacdPreset = MACD_PRESETS.find(
    (preset) => preset.params.fast === currentMacdParams.fast
      && preset.params.slow === currentMacdParams.slow
      && preset.params.signal === currentMacdParams.signal,
  )?.id

  function updateMaSetting(index: number, patch: Partial<MaSetting>) {
    setMaSettings((current) => ({
      ...current,
      [timeframe]: current[timeframe].map((setting, item) =>
        item === index ? { ...setting, ...patch } : setting,
      ),
    }))
  }

  const refresh = useCallback(async () => {
    const [state, availableIndices] = await Promise.all([
      api<SimState>('/api/sim/state'),
      api<CustomIndex[]>('/api/indices'),
    ])
    setSim(state)
    if (state.initial_cash !== undefined) setSeedCash(state.initial_cash)
    if (state.fee_settings) {
      setCommissionRate(state.fee_settings.commission_rate)
      setMinimumCommission(state.fee_settings.minimum_commission)
      setStampDutyRate(state.fee_settings.stamp_duty_rate)
      setTransferFeeRate(state.fee_settings.transfer_fee_rate)
    }
    setIndices(availableIndices)
    if (!selectedIndex && availableIndices[0]) setSelectedIndex(availableIndices[0].id)
  }, [selectedIndex])

  useEffect(() => {
    void refresh().catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : '连接后端失败')
    })
  }, [refresh])

  useEffect(() => {
    const visibleStart = sim.bars[0]?.date ?? sim.start_date
    if (!sim.active || !comparisonId || !visibleStart || !sim.current_date) {
      setComparisonBars([])
      return
    }
    const query = new URLSearchParams({ start: visibleStart, end: sim.current_date })
    void api<Bar[]>(`/api/indices/${comparisonId}/daily?${query.toString()}`)
      .then(setComparisonBars)
      .catch((error: unknown) => setMessage(error instanceof Error ? error.message : '读取对照指数失败'))
  }, [comparisonId, sim.active, sim.bars, sim.current_date, sim.start_date])

  async function perform(action: () => Promise<SimState>, success?: string) {
    setBusy(true)
    setMessage('')
    try {
      setSim(await action())
      if (success) setMessage(success)
      const updatedIndices = await api<CustomIndex[]>('/api/indices')
      setIndices(updatedIndices)
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const startSimulation = () =>
    perform(
      () =>
        api<SimState>('/api/sim/start', {
          method: 'POST',
          body: JSON.stringify({
            asset_type: assetKind,
            asset_id: assetKind === 'stock' ? stockCode : selectedIndex,
            start_date: startDate,
            adjust: sim.adjust,
          }),
        }),
      '模拟已开始',
    )

  const submitOrder = (side: 'buy' | 'sell') =>
    perform(
      () =>
        api<SimState>('/api/sim/orders', {
          method: 'POST',
          body: JSON.stringify({ side, quantity: Number(quantity) }),
        }),
      side === 'buy' ? '买入委托已提交，推进下一根后模拟成交' : '卖出委托已提交，推进下一根后模拟成交',
    )

  const createIndex = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true)
    setMessage('正在下载成分股日线并计算指数…')
    try {
      const codes = indexCodes.split(/[\s,，;；]+/).filter(Boolean)
      const weights = indexWeights.trim()
        ? indexWeights.split(/[\s,，;；]+/).filter(Boolean).map(Number)
        : null
      if (weights && weights.length !== codes.length) {
        throw new Error('权重数量必须与成分股数量相同')
      }
      if (weights?.some((weight) => !Number.isFinite(weight) || weight <= 0)) {
        throw new Error('权重必须为大于 0 的数字')
      }
      const components = codes.map((code, index) => ({
        code,
        ...(weights ? { weight: weights[index] } : {}),
      }))
      const created = await api<CustomIndex>('/api/indices', {
        method: 'POST',
        body: JSON.stringify({ name: indexName, components }),
      })
      const updatedIndices = await api<CustomIndex[]>('/api/indices')
      setIndices(updatedIndices)
      setSelectedIndex(created.id)
      setAssetKind('custom_index')
      setMessage(`已创建「${created.name}」，基日 ${created.base_date}`)
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : '创建指数失败')
    } finally {
      setBusy(false)
    }
  }

  async function updateMode(priceMode: SimState['price_mode']) {
    await perform(() =>
      api<SimState>('/api/sim/settings', {
        method: 'PATCH',
        body: JSON.stringify({ price_mode: priceMode }),
      }),
    )
  }

  async function updateAdjust(adjust: SimState['adjust']) {
    await perform(() =>
      api<SimState>('/api/sim/settings', {
        method: 'PATCH',
        body: JSON.stringify({ adjust }),
      }),
    )
  }

  async function drawRandomStock() {
    setBusy(true)
    setMessage('正在随机抽取股票…')
    try {
      const result = await api<{ code: string; name: string }>('/api/stocks/random?pool=' + stockPool)
      setStockCode(result.code)
      setAssetKind('stock')
      setMessage(`随机选中：${result.name}（${result.code}）`)
    } catch (error: unknown) {
      setMessage(error instanceof Error ? error.message : '随机抽取失败')
    } finally {
      setBusy(false)
    }
  }

  async function updateFees() {
    await perform(() =>
      api<SimState>('/api/sim/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          fees: {
            commission_rate: commissionRate,
            minimum_commission: minimumCommission,
            stamp_duty_rate: stampDutyRate,
            transfer_fee_rate: transferFeeRate,
          },
        }),
      }),
      '模拟费率已更新',
    )
  }

  const chartBar = chartBars.at(-1)
  const portfolio = sim.portfolio
  const instrumentLabel =
    sim.asset_type === 'custom_index'
      ? indices.find((index) => index.id === sim.asset_id)?.name ?? '自定义指数'
      : sim.asset_id ?? '未选择标的'

  const orderPrice = sim.current_bar?.close ?? 0
  const orderLot = (sim.asset_type ?? 'stock') === 'stock' ? 100 : 1
  const availableCash = portfolio?.cash ?? sim.initial_cash ?? seedCash

  function feesFor(side: 'buy' | 'sell', gross: number): number {
    const assetType = sim.asset_type ?? 'stock'
    const assetId = sim.asset_id ?? ''
    const commission = Math.max(minimumCommission, gross * commissionRate)
    const stamp = side === 'sell' ? gross * stampDutyRate : 0
    const transfer = assetType === 'stock' && assetId.startsWith('6')
      ? gross * transferFeeRate
      : 0
    return Math.round((commission + stamp + transfer) * 100) / 100
  }

  function maxQuantityForPct(pct: number): number {
    if (orderPrice <= 0) return 0
    const budget = availableCash * pct
    let low = 0
    let high = Math.floor(budget / (orderPrice * orderLot))
    while (low < high) {
      const mid = Math.ceil((low + high) / 2)
      const gross = mid * orderLot * orderPrice
      if (gross + feesFor('buy', gross) <= budget + 1e-9) low = mid
      else high = mid - 1
    }
    return low * orderLot
  }

  function applyAllocation(pct: number) {
    setQuantity(maxQuantityForPct(pct))
    setAllocationPct(pct)
  }

  const estimatedBuyGross = orderPrice * quantity
  const estimatedSellGross = orderPrice * quantity
  const estimatedBuyFees = quantity > 0 ? feesFor('buy', estimatedBuyGross) : 0
  const estimatedSellFees = quantity > 0 ? feesFor('sell', estimatedSellGross) : 0

  return (
    <main className="app-shell">
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <header className="topbar glass-card">
        <div className="brand-lockup">
          <div className="brand-mark">✿</div>
          <div>
            <div className="brand-name">股票<span>模拟</span></div>
            <div className="brand-caption">A 股日线模拟器</div>
          </div>
        </div>
        <div className="topbar-right">
          <span className="tiny-sparkle">✧</span>
          <span className="sim-pill">模拟盘</span>
          <span className="user-chip" title={user.username}>♡ {user.username}</span>
          {user.is_admin && <a className="admin-link" href="/admin">管理后台</a>}
          <button className="logout-button" onClick={onLogout}>退出</button>
        </div>
      </header>

      <section className="workspace">
        <aside className="left-column">
          <section className="hero-card glass-card">
            <div className="hero-copy">
              <span className="eyebrow"><span className="eyebrow-star">✦</span> DAILY SIMULATION</span>
              <h1>日线模拟交易</h1>
              <p>选择标的并逐日推进行情。</p>
            </div>
            <div className="hero-doodle" aria-hidden="true">
              <span className="doodle-star star-a">✦</span>
              <span className="doodle-star star-b">✧</span>
              <span className="doodle-orbit">◌</span>
              <span className="doodle-heart">♡</span>
            </div>
            <div className="hero-bottom">
              <div className="instrument-title">
                <span className="instrument-icon">⌁</span>
                <span>{instrumentLabel}</span>
                <span className="instrument-chip">{sim.asset_type === 'custom_index' ? '自定义指数' : 'A 股'}</span>
              </div>
              <div className="day-chip">{sim.current_date ?? '准备开始'} <span>☼</span></div>
            </div>
          </section>

          <section className="side-card glass-card setup-card">
            <div className="side-heading"><span className="number-bubble">01</span><div><h3>标的设置</h3><small>选择股票或自定义指数</small></div><span className="side-sparkle">✧</span></div>
            <div className="segmented">
              <button className={assetKind === 'stock' ? 'selected' : ''} onClick={() => setAssetKind('stock')}>A 股</button>
              <button className={assetKind === 'custom_index' ? 'selected' : ''} onClick={() => setAssetKind('custom_index')}>自定义指数</button>
            </div>
            {assetKind === 'stock' ? (
              <>
                <label className="field-label">股票代码<div className="input-wrap"><span>⌕</span><input value={stockCode} onChange={(event) => setStockCode(event.target.value)} maxLength={6} inputMode="numeric" /></div></label>
                <div className="random-row"><select value={stockPool} onChange={(event) => setStockPool(event.target.value as typeof stockPool)}><option value="all">沪深主板 + 创业板</option><option value="hs300">沪深 300</option><option value="sz50">上证 50</option><option value="zz500">中证 500</option></select><button onClick={() => void drawRandomStock()} disabled={busy}>随机抽一只 ✧</button></div>
              </>
            ) : (
              <label className="field-label">选择指数<select value={selectedIndex} onChange={(event) => setSelectedIndex(event.target.value)}><option value="">请选择</option>{indices.map((index) => <option value={index.id} key={index.id}>{index.name}</option>)}</select></label>
            )}
            <label className="field-label">模拟起始日<div className="input-wrap date-input"><span>☼</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></div></label>
            <div className="field-label">图表价格口径<div className="toggle-row"><button className={sim.adjust === 'forward' ? 'toggle-active' : ''} onClick={() => void updateAdjust('forward')}>前复权</button><button className={sim.adjust === 'none' ? 'toggle-active' : ''} onClick={() => void updateAdjust('none')}>不复权</button></div></div>
            <button className="primary-button" onClick={() => void startSimulation()} disabled={busy || (assetKind === 'custom_index' && !selectedIndex)}><span>开始模拟</span><b>✦</b></button>
            <p className="tiny-disclaimer">行情依据历史日线模拟 · 不代表真实成交</p>
          </section>

          <section className="side-card glass-card order-card">
            <div className="side-heading"><span className="number-bubble peach-bubble">02</span><div><h3>下单</h3><small>推进下一根 K 线后撮合</small></div><span className="sim-pill order-sim-pill">模拟盘</span></div>
            <div className="order-mode"><span>成交价格</span><select value={sim.price_mode} onChange={(event) => void updateMode(event.target.value as SimState['price_mode'])}>
              <option value="open">下一根开盘价</option><option value="close">下一根收盘价</option><option value="mid">开收中间值</option><option value="random">蜡烛范围随机</option>
            </select></div>
            <label className="field-label qty-label">数量（{sim.asset_type === 'custom_index' ? '份' : '股'}）<div className="quantity-box"><button onClick={() => { setAllocationPct(null); setQuantity((value) => Math.max(orderLot, value - orderLot)) }}>−</button><input type="number" min={orderLot} step={orderLot} value={quantity} onChange={(event) => { setAllocationPct(null); setQuantity(Number(event.target.value)) }} /><button onClick={() => { setAllocationPct(null); setQuantity((value) => value + orderLot) }}>＋</button></div></label>
            <div className="allocation-row">
              <span>按资金</span>
              <div className="allocation-buttons">
                {[0.25, 0.5, 0.75, 1].map((pct) => (
                  <button
                    key={pct}
                    className={allocationPct === pct ? 'active' : ''}
                    disabled={!sim.active || busy || orderPrice <= 0}
                    onClick={() => applyAllocation(pct)}
                  >
                    {pct * 100}%
                  </button>
                ))}
              </div>
            </div>
            <div className="order-estimate">
              <div className="order-estimate-row"><span>当前价</span><strong>{orderPrice > 0 ? orderPrice.toFixed(2) : '—'}</strong></div>
              <div className="order-estimate-row"><span>预计买入总额</span><strong>{orderPrice > 0 ? money(estimatedBuyGross + estimatedBuyFees) : '—'}</strong></div>
              <div className="order-estimate-row"><span>预计卖出到账</span><strong>{orderPrice > 0 ? money(estimatedSellGross - estimatedSellFees) : '—'}</strong></div>
              <span className="estimate-note">含预估费用 · 按当前收盘价估算，实际以下一根成交价为准</span>
            </div>
            <div className="order-buttons"><button className="buy-button" disabled={!sim.active || busy || !!sim.pending_order || quantity <= 0 || quantity % orderLot !== 0} onClick={() => void submitOrder('buy')}>买 入 <span>♡</span></button><button className="sell-button" disabled={!sim.active || busy || !!sim.pending_order || quantity <= 0 || quantity % orderLot !== 0} onClick={() => void submitOrder('sell')}>卖 出 <span>↗</span></button></div>
            {sim.pending_order && <div className="pending-note">✧ {sim.pending_order.side === 'buy' ? '买入' : '卖出'} {sim.pending_order.quantity}，等待下一根 K 线</div>}
            <div className="fee-note">模拟费率：佣金、印花税按设置计算</div>
          </section>

          <details className="side-card glass-card custom-index-card">
            <summary><span className="number-bubble lilac-bubble">03</span><div><h3>创建自定义指数</h3><small>配置指数名称、成分股和权重</small></div><span className="details-arrow">⌄</span></summary>
            <form onSubmit={(event) => void createIndex(event)} className="index-form">
              <label className="field-label">指数名称<input value={indexName} onChange={(event) => setIndexName(event.target.value)} maxLength={40} /></label>
              <label className="field-label">成分股代码<textarea value={indexCodes} onChange={(event) => setIndexCodes(event.target.value)} rows={3} placeholder="每行一个，例如 600000" /></label>
              <label className="field-label">权重（可选，留空为等权）<textarea value={indexWeights} onChange={(event) => setIndexWeights(event.target.value)} rows={2} placeholder="顺序对应代码，如 1 3" /></label>
              <button className="secondary-button" disabled={busy}>创建指数 <span>✧</span></button>
              <small className="index-footnote">基点 1000 · 自动取共同基日 · 非官方指数</small>
            </form>
          </details>

          <section className="side-card glass-card settings-card">
            <div className="side-heading"><span className="number-bubble mint-bubble">04</span><div><h3>账户设置</h3><small>账户数据保存在本地</small></div></div>
            <div className="settings-line"><span>账户权益</span><strong>{money(sim.portfolio?.total_asset ?? sim.initial_cash ?? seedCash)} <small>元</small></strong></div>
            <label className="field-label">重置后的起始资金（元）<input type="number" min="1" step="10000" value={seedCash} onChange={(event) => setSeedCash(Number(event.target.value))} /></label>
            <button className="reset-button" disabled={busy} onClick={() => {
              if (window.confirm('要重置模拟账户吗？持仓和成交记录会清空。')) {
                void perform(() => api<SimState>('/api/sim/reset', { method: 'POST', body: JSON.stringify({ initial_cash: seedCash }) }), '账户已重置')
              }
            }}>用这笔资金重置账户 <span>↻</span></button>
            <details className="fee-settings">
              <summary>模拟费率设置 <span>＋</span></summary>
              <label>佣金比例<input type="number" min="0" step="0.00001" value={commissionRate} onChange={(event) => setCommissionRate(Number(event.target.value))} /></label>
              <label>最低佣金（元）<input type="number" min="0" step="1" value={minimumCommission} onChange={(event) => setMinimumCommission(Number(event.target.value))} /></label>
              <label>印花税比例<input type="number" min="0" step="0.0001" value={stampDutyRate} onChange={(event) => setStampDutyRate(Number(event.target.value))} /></label>
              <label>过户费比例<input type="number" min="0" step="0.00001" value={transferFeeRate} onChange={(event) => setTransferFeeRate(Number(event.target.value))} /></label>
              <button className="secondary-button" onClick={() => void updateFees()}>保存费率</button>
            </details>
          </section>
        </aside>

        <div className="chart-column">
          <section className="chart-card glass-card">
            <div className="chart-heading">
              <div>
                <div className="section-kicker">{timeframe === 'day' ? '日线' : timeframe === 'week' ? '周线' : '月线'}行情 <span>✧</span></div>
                <h2>K 线</h2>
              </div>
              {chartBar && (
                <div className="quote-block">
                  <span className="quote-label">收盘</span>
                  <strong className={chartBar.close >= chartBar.open ? 'up-text' : 'down-text'}>{chartBar.close.toFixed(2)}</strong>
                  <span className="quote-date">截至 {sim.current_date}</span>
                </div>
              )}
            </div>
            <div className="chart-legend">
              <span><i className="legend-dot up-dot" /> 上涨</span>
              <span><i className="legend-dot down-dot" /> 下跌</span>
              <span className="legend-note">无网格 · {sim.adjust === 'forward' ? '前复权' : '不复权'}</span>
            </div>
            <div className="chart-toolbar">
              <div className="timeframe-tabs" aria-label="K线周期">
                <button className={timeframe === 'day' ? 'active' : ''} onClick={() => setTimeframe('day')}>日</button>
                <button className={timeframe === 'week' ? 'active' : ''} onClick={() => setTimeframe('week')}>周</button>
                <button className={timeframe === 'month' ? 'active' : ''} onClick={() => setTimeframe('month')}>月</button>
              </div>
              {sim.bars.length > 0 && (
                <div className="comparison-select"><span>图表对照</span><select value={comparisonId} onChange={(event) => setComparisonId(event.target.value)}><option value="">不叠加</option>{indices.filter((index) => index.id !== (sim.asset_type === 'custom_index' ? sim.asset_id : '')).map((index) => <option value={index.id} key={index.id}>{index.name} · 自定义</option>)}</select>{comparisonId && <i className="comparison-dot" />}</div>
              )}
            </div>
            {sim.bars.length ? (
              <KlineChart
                bars={chartBars}
                comparison={comparisonBars}
                maSettings={currentMaSettings}
                macdPoints={macdPoints}
                macdPreheat={macdPreheat}
                showCandles={showCandles}
                timeframe={timeframe}
                resetKey={`${sim.asset_type}:${sim.asset_id}:${sim.start_date}`}
              />
            ) : (
              <div className="chart-empty">
                <span className="empty-cloud">☁</span>
                <strong>选择股票或自定义指数开始模拟</strong>
                <small>行情按日线展示</small>
              </div>
            )}
            <div className="chart-footer">
              <span>{timeframe === 'day' ? '日线' : timeframe === 'week' ? '周线' : '月线'}显示 · 逐交易日推进</span>
              <button className="step-button" onClick={() => void perform(() => api<SimState>('/api/sim/step', { method: 'POST' }))} disabled={!sim.active || busy}>
                推进下一交易日 <span>→</span>
              </button>
            </div>
          </section>
        </div>

        <aside className="right-column">
          <section className="mini-card glass-card indicators-card">
            <div className="mini-title"><span className="mini-icon lavender">⌁</span><div><strong>指标与周期</strong><small>均线窗口按当前周期的 K 线根数计算</small></div></div>
            <div className="indicator-controls">
              <label className="indicator-toggle"><input type="checkbox" checked={showCandles} onChange={(event) => setShowCandles(event.target.checked)} /><span>显示 K 线</span></label>
              {currentMaSettings.map((setting, index) => (
                <label className="ma-control" key={`${timeframe}-${index}`}>
                  <input type="checkbox" checked={setting.visible} onChange={(event) => updateMaSetting(index, { visible: event.target.checked })} />
                  <i style={{ '--ma-color': MA_COLORS[index % MA_COLORS.length] } as CSSProperties} />
                  <span>MA</span>
                  <input aria-label={`MA${index + 1}周期`} type="number" min="2" max="1000" value={setting.period} onChange={(event) => updateMaSetting(index, { period: Math.max(2, Number(event.target.value)) })} />
                </label>
              ))}
            </div>
            <div className="macd-settings">
              <div className="macd-caption"><strong>MACD</strong></div>
              <div className="macd-presets" role="group" aria-label="MACD 参数预设">
                {MACD_PRESETS.map((preset) => (
                  <button
                    type="button"
                    key={preset.id}
                    className={activeMacdPreset === preset.id ? 'active' : ''}
                    onClick={() => setMacdParams((current) => ({ ...current, [timeframe]: preset.params }))}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              <label>快线<input type="number" min="1" value={currentMacdParams.fast} onChange={(event) => setMacdParams((current) => ({ ...current, [timeframe]: { ...current[timeframe], fast: Number(event.target.value) } }))} /></label>
              <label>慢线<input type="number" min="2" value={currentMacdParams.slow} onChange={(event) => setMacdParams((current) => ({ ...current, [timeframe]: { ...current[timeframe], slow: Number(event.target.value) } }))} /></label>
              <label>信号线<input type="number" min="2" value={currentMacdParams.signal} onChange={(event) => setMacdParams((current) => ({ ...current, [timeframe]: { ...current[timeframe], signal: Number(event.target.value) } }))} /></label>
              {!macdValid && <small className="macd-error">参数要求：快线小于慢线，信号线大于 1。</small>}
            </div>
          </section>

          <section className="mini-card glass-card account-card">
            <div className="mini-title"><span className="mini-icon peach">♡</span><div><strong>模拟账户</strong><small>仅供本地练习</small></div></div>
            <div className="account-metrics">
              <div><small>总资产</small><strong>{money(portfolio?.total_asset ?? sim.initial_cash)}</strong></div>
              <div><small>可用现金</small><strong>{money(portfolio?.cash ?? sim.initial_cash)}</strong></div>
              <div><small>持仓浮盈</small><strong className={(portfolio?.unrealized_pnl ?? 0) >= 0 ? 'up-text' : 'down-text'}>{money(portfolio?.unrealized_pnl)}</strong></div>
            </div>
            <div className="holdings-line">当前持仓 <b>{portfolio?.quantity ?? 0}</b> {sim.asset_type === 'custom_index' ? '份' : '股'} <span>· 可卖 {portfolio?.available_to_sell ?? 0}</span></div>
          </section>

          <section className="trades-card glass-card">
            <div className="section-head"><div><span className="section-kicker">TRADES</span><h2>成交记录</h2></div><span className="count-badge">{sim.recent_trades?.length ?? 0} 笔</span></div>
            {sim.recent_trades?.length ? (
              <div className="trade-list">
                {[...sim.recent_trades].reverse().slice(0, 6).map((trade, index) => (
                  <div className="trade-row" key={`${trade.date}-${trade.side}-${index}`}>
                    <span className={`trade-side ${trade.side}`}>{trade.side === 'buy' ? '买入' : '卖出'}</span>
                    <span>{trade.date}</span><span>{trade.quantity} {sim.asset_type === 'custom_index' ? '份' : '股'}</span>
                    <strong>{money(trade.price)}</strong><span className={trade.status === 'filled' ? 'filled-tag' : 'rejected-tag'}>{trade.status === 'filled' ? '已成交' : trade.reason ?? '已拒绝'}</span>
                  </div>
                ))}
              </div>
            ) : <div className="empty-trades">暂无成交记录</div>}
          </section>
        </aside>
      </section>

      {message && <div className="toast-message" role="status"><span>✦</span>{message}<button onClick={() => setMessage('')}>×</button></div>}
      <footer className="page-footer">仅供学习与研究，不构成投资建议</footer>
    </main>
  )
}

function LoginScreen({ onLogin }: { onLogin: (user: AuthUser) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const user = await api<AuthUser>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      onLogin(user)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-shell">
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <form className="auth-card glass-card" onSubmit={(event) => void submit(event)}>
        <div className="auth-brand">
          <div className="brand-mark">✿</div>
          <div>
            <div className="brand-name">股票<span>模拟</span></div>
            <div className="brand-caption">A 股日线模拟器</div>
          </div>
        </div>
        <h1>朋友，请登录</h1>
        <p className="auth-note">账号由管理员手动开通，不开放注册。</p>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label className="field-label">用户名
          <div className="input-wrap"><span>♡</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" autoFocus required />
          </div>
        </label>
        <label className="field-label">密码
          <div className="input-wrap"><span>✧</span>
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
          </div>
        </label>
        <button className="primary-button" disabled={busy}>
          <span>{busy ? '登录中…' : '进入模拟器'}</span><b>✦</b>
        </button>
        <p className="tiny-disclaimer">仅供学习与研究，不构成投资建议</p>
      </form>
    </main>
  )
}

function App() {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    let alive = true
    const expire = () => setUser(null)
    window.addEventListener('auth-expired', expire)
    api<AuthUser>('/api/auth/me')
      .then((current) => {
        if (alive) setUser(current)
      })
      .catch(() => {
        if (alive) setUser(null)
      })
      .finally(() => {
        if (alive) setChecking(false)
      })
    return () => {
      alive = false
      window.removeEventListener('auth-expired', expire)
    }
  }, [])

  async function logout() {
    try {
      await api<void>('/api/auth/logout', { method: 'POST' })
    } finally {
      setUser(null)
    }
  }

  if (checking) {
    return (
      <main className="auth-shell">
        <div className="auth-card glass-card auth-checking">正在检查登录状态…</div>
      </main>
    )
  }
  if (!user) return <LoginScreen onLogin={setUser} />
  return <TradingApp user={user} onLogout={() => void logout()} />
}

export default App
