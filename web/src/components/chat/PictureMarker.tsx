import { useCallback, useEffect, useRef, useState, type PointerEvent } from 'react'
import { Modal } from '@/components/ui/Modal'
import { Button, Spinner } from '@/components/ui/primitives'
import { useTranslation } from '@/hooks/useTranslation'
import { chatApi } from '@/services/chat'
import { ApiError } from '@/services/client'
import type { ChatPicture, PictureMark } from '@/types/api'

/** The longest side the picture is sent at: enough to read print, small to send. */
const SEND_SIDE = 2200
const THUMB_SIDE = 360
const STROKE = '#e5484d'

type Point = { x: number; y: number }

/**
 * A picture, and a pen to circle what the question is about.
 *
 * Each stroke becomes a marked region — its bounding box — which the server reads on its
 * own, so "what does this mean?" arrives with the exact line the reader circled. The
 * picture is drawn on a canvas sized to the screen; strokes are kept as fractions of the
 * picture, so they mean the same place at any size.
 */
export function PictureMarker({
  file,
  onClose,
  onAttach,
}: {
  file: File | null
  onClose: () => void
  onAttach: (picture: ChatPicture) => void
}) {
  const { t } = useTranslation()
  const canvas = useRef<HTMLCanvasElement>(null)
  const [image, setImage] = useState<HTMLImageElement | null>(null)
  const [strokes, setStrokes] = useState<Point[][]>([])
  const drawing = useRef<Point[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setStrokes([])
    setError(null)
    setImage(null)
    if (!file) return
    const url = URL.createObjectURL(file)
    const picture = new Image()
    picture.onload = () => setImage(picture)
    picture.onerror = () => setError(t('picture.unreadable'))
    picture.src = url
    return () => URL.revokeObjectURL(url)
  }, [file, t])

  const paint = useCallback(
    (target: HTMLCanvasElement, source: HTMLImageElement, lines: Point[][], width: number) => {
      const height = Math.round((source.naturalHeight / source.naturalWidth) * width)
      target.width = width
      target.height = height
      const context = target.getContext('2d')
      if (!context) return
      context.drawImage(source, 0, 0, width, height)
      context.strokeStyle = STROKE
      context.lineWidth = Math.max(3, width / 220)
      context.lineCap = 'round'
      context.lineJoin = 'round'
      for (const line of lines) {
        context.beginPath()
        line.forEach((point, index) => {
          const x = point.x * width
          const y = point.y * height
          if (index === 0) context.moveTo(x, y)
          else context.lineTo(x, y)
        })
        context.stroke()
      }
    },
    [],
  )

  const redraw = useCallback(
    (lines: Point[][]) => {
      const node = canvas.current
      if (!node || !image) return
      const width = Math.min(image.naturalWidth, node.parentElement?.clientWidth ?? 640)
      paint(node, image, lines, width)
    },
    [image, paint],
  )

  useEffect(() => redraw(strokes), [redraw, strokes])

  const point = (event: PointerEvent<HTMLCanvasElement>): Point => {
    const box = event.currentTarget.getBoundingClientRect()
    return {
      x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
      y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)),
    }
  }

  const onDown = (event: PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    drawing.current = [point(event)]
  }
  const onMove = (event: PointerEvent<HTMLCanvasElement>) => {
    if (!drawing.current) return
    drawing.current.push(point(event))
    redraw([...strokes, drawing.current])
  }
  const onUp = () => {
    const line = drawing.current
    drawing.current = null
    if (line && line.length > 1) setStrokes((current) => [...current, line])
  }

  const attach = async () => {
    if (!image) return
    setBusy(true)
    setError(null)
    try {
      const marks: PictureMark[] = strokes.map((line) => {
        const xs = line.map((p) => p.x)
        const ys = line.map((p) => p.y)
        const x = Math.min(...xs)
        const y = Math.min(...ys)
        return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }
      })
      const scaled = document.createElement('canvas')
      paint(scaled, image, [], Math.min(image.naturalWidth, SEND_SIDE))
      const blob = await new Promise<Blob | null>((resolve) => scaled.toBlob(resolve, 'image/jpeg', 0.9))
      if (!blob) throw new Error('encode')
      const read = await chatApi.readPicture(blob, marks)
      const thumb = document.createElement('canvas')
      paint(thumb, image, strokes, Math.min(image.naturalWidth, THUMB_SIDE))
      onAttach({
        thumbnail: thumb.toDataURL('image/jpeg', 0.75),
        text: read.text,
        marked: read.marked,
        vision: read.vision ?? '',
        hasText: read.has_text,
      })
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : t('picture.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={file !== null}
      title={t('picture.title')}
      onClose={busy ? () => undefined : onClose}
      wide
      footer={
        <>
          <Button variant="ghost" size="sm" icon="undo" disabled={!strokes.length || busy}
            onClick={() => setStrokes((current) => current.slice(0, -1))}>
            {t('picture.undo')}
          </Button>
          <Button variant="ghost" size="sm" disabled={!strokes.length || busy} onClick={() => setStrokes([])}>
            {t('picture.clear')}
          </Button>
          <span style={{ flex: 1 }} />
          <Button variant="secondary" size="sm" disabled={busy} onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button size="sm" disabled={!image || busy} onClick={attach}>
            {busy ? <Spinner size={13} /> : null} {busy ? t('picture.reading') : t('picture.attach')}
          </Button>
        </>
      }
    >
      <p className="picture-marker__hint">{t('picture.hint')}</p>
      <div className="picture-marker__stage">
        {image ? (
          <canvas
            ref={canvas}
            className="picture-marker__canvas"
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
          />
        ) : (
          !error && <Spinner size={18} />
        )}
      </div>
      {error && <p className="picture-marker__error" role="alert">{error}</p>}
    </Modal>
  )
}
