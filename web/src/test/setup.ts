import '@testing-library/jest-dom/vitest'

// jsdom implements neither of these, and components that measure the viewport or scroll
// a citation into view would otherwise throw during a test rather than fail on the
// behaviour being tested.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {}
}
