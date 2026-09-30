import { create } from 'zustand'

export type ToastTone = 'success' | 'error' | 'info' | 'warning'

export interface Toast {
  id: string
  tone: ToastTone
  title: string
  description?: string
  /** Milliseconds before it dismisses itself; 0 keeps it until dismissed. */
  duration: number
}

interface ToastState {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id' | 'duration'> & { duration?: number }) => string
  dismiss: (id: string) => void
  clear: () => void
}

const DEFAULT_DURATION = 4500
const MAX_VISIBLE = 4

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: ({ duration = DEFAULT_DURATION, ...toast }) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    set((state) => ({ toasts: [...state.toasts, { ...toast, id, duration }].slice(-MAX_VISIBLE) }))
    return id
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}))

/** Shorthands, so a component does not have to name the tone every time. */
export const toast = {
  success: (title: string, description?: string) =>
    useToasts.getState().push({ tone: 'success', title, description }),
  error: (title: string, description?: string) =>
    useToasts.getState().push({ tone: 'error', title, description, duration: 7000 }),
  info: (title: string, description?: string) =>
    useToasts.getState().push({ tone: 'info', title, description }),
  warning: (title: string, description?: string) =>
    useToasts.getState().push({ tone: 'warning', title, description }),
}
