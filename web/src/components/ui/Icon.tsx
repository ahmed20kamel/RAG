import type { SVGProps } from 'react'

/**
 * The icon set, drawn inline.
 *
 * An icon font or package would add a network request and a licence to track for a
 * couple of dozen glyphs. These are stroke-based and inherit `currentColor`, so they
 * follow the theme without a second definition.
 */

export type IconName =
  | 'chat'
  | 'library'
  | 'upload'
  | 'settings'
  | 'plus'
  | 'search'
  | 'send'
  | 'stop'
  | 'copy'
  | 'check'
  | 'refresh'
  | 'thumbUp'
  | 'thumbDown'
  | 'trash'
  | 'archive'
  | 'close'
  | 'menu'
  | 'chevronDown'
  | 'chevronEnd'
  | 'chevronStart'
  | 'document'
  | 'sparkles'
  | 'alert'
  | 'info'
  | 'sun'
  | 'moon'
  | 'monitor'
  | 'globe'
  | 'externalLink'
  | 'layers'
  | 'tag'
  | 'clock'
  | 'database'
  | 'cpu'
  | 'inbox'
  | 'file'
  | 'shield'
  | 'keyboard'
  | 'image'
  | 'mic'
  | 'pen'
  | 'undo'
  | 'download'
  | 'chart'

const PATHS: Record<IconName, string> = {
  chat: 'M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.9-.9L3 21l1.9-4.1A8.4 8.4 0 0 1 4 12.5a8.4 8.4 0 0 1 9-8.4 8.4 8.4 0 0 1 8 7.4z',
  library: 'M4 19.5V6a2 2 0 0 1 2-2h12a1 1 0 0 1 1 1v14M6 17h13M6 21h13a1 1 0 0 0 1-1v-3H6a1.5 1.5 0 0 0 0 3z',
  upload: 'M12 15V3m0 0L8 7m4-4 4 4M3 15v4a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4',
  settings:
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.6 7a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H7a1.7 1.7 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V7a1.7 1.7 0 0 0 1.5 1H23a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.35-4.35',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4 20-7z',
  stop: 'M7 7h10v10H7z',
  copy: 'M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1',
  check: 'M20 6 9 17l-5-5',
  refresh: 'M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6',
  thumbUp: 'M7 21H4a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1h3m0 10 4 1a2 2 0 0 0 2-1l2.5-7a2 2 0 0 0-2-2.7H13l.6-3.4A2 2 0 0 0 11.7 2L7 11z',
  thumbDown: 'M17 3h3a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-3m0-10-4-1a2 2 0 0 0-2 1L5.5 9a2 2 0 0 0 2 2.7H11l-.6 3.4a2 2 0 0 0 1.9 2.4L17 13z',
  trash: 'M4 7h16M10 11v6M14 11v6M5 7l1 13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1l1-13M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3',
  archive: 'M3 8h18v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8zM3 8V5a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v3M10 12h4',
  close: 'M18 6 6 18M6 6l12 12',
  menu: 'M4 7h16M4 12h16M4 17h16',
  chevronDown: 'm6 9 6 6 6-6',
  chevronEnd: 'm9 18 6-6-6-6',
  chevronStart: 'm15 18-6-6 6-6',
  document: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6',
  sparkles: 'M12 3l1.9 4.6L18.5 9.5 13.9 11.4 12 16l-1.9-4.6L5.5 9.5l4.6-1.9zM19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z',
  alert: 'M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16v-4M12 8h.01',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  monitor: 'M3 5h18v11H3zM8 21h8M12 16v5',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18z',
  externalLink: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  layers: 'm12 2 9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5',
  tag: 'M20.6 13.4 12 22l-9-9V3h10l7.6 7.6a2 2 0 0 1 0 2.8zM7.5 7.5h.01',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  database: 'M12 8c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zM4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  cpu: 'M6 6h12v12H6zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3M10 10h4v4h-4z',
  inbox: 'M21 12h-5l-2 3h-4l-2-3H3M6 4h12l3 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6z',
  file: 'M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM13 2v7h7',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4',
  keyboard: 'M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M17 10h.01M7 14h10',
  image: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21',
  mic: 'M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3zM19 10v2a7 7 0 0 1-14 0v-2M12 19v3M8 22h8',
  pen: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
  undo: 'M3 7v6h6M3 13a9 9 0 1 0 3-7.7L3 7',
  download: 'M12 3v12m0 0-4-4m4 4 4-4M3 15v4a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4',
  chart: 'M3 3v18h18M7 15l4-4 3 3 6-7',
}

const FILLED = new Set<IconName>(['stop'])

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 18, ...rest }: IconProps) {
  const filled = FILLED.has(name)
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill={filled ? 'currentColor' : 'none'}
      stroke={filled ? 'none' : 'currentColor'}
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
