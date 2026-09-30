import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { applyPreferences, usePreferences } from '@/state/preferences'
import './styles/tokens.css'
import './styles/base.css'

// Applied before the first paint so the page does not flash the wrong theme or
// direction while React mounts.
const { theme, language } = usePreferences.getState()
applyPreferences(theme, language)

const container = document.getElementById('root')
if (!container) throw new Error('Missing #root')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
