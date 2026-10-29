import { readFileSync } from 'node:fs'
import path from 'node:path'

import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import { beforeAll, describe, expect, it } from 'vitest'

import tailwindConfig from '../../tailwind.config.js'

const tokens = readFileSync(path.join(__dirname, 'tokens.css'), 'utf8')

// Exercise Tailwind's emitted CSS, not a second implementation of its config.
// The interface follows the dev branch: Tailwind's default palette, type scale,
// radii and shadows stay untouched, and the semantic tokens (kept for screens dev
// does not have) stay wired to their CSS variables.
const colorUtilities: Record<string, [string, string]> = {
  'bg-canvas': ['background-color', '--bg-canvas'],
  'bg-surface': ['background-color', '--bg-surface'],
  'bg-subtle': ['background-color', '--bg-subtle'],
  'bg-hover': ['background-color', '--bg-hover'],
  'bg-selected': ['background-color', '--bg-selected'],
  'text-fg': ['color', '--text-primary'],
  'text-fg-secondary': ['color', '--text-secondary'],
  'text-fg-muted': ['color', '--text-muted'],
  'text-fg-placeholder': ['color', '--text-placeholder'],
  'text-fg-link': ['color', '--text-link'],
  'text-fg-disabled': ['color', '--disabled-text'],
  'border-line': ['border-color', '--border-subtle'],
  'border-line-control': ['border-color', '--border-control'],
  'bg-action': ['background-color', '--action-primary'],
  'bg-action-hover': ['background-color', '--action-hover'],
  'bg-action-pressed': ['background-color', '--action-pressed'],
  'text-action-fg': ['color', '--action-onPrimary'],
  'bg-action-danger': ['background-color', '--action-danger'],
  'text-action-danger-fg': ['color', '--action-onDanger'],
  'outline-focus': ['outline-color', '--focus-ring'],
  'bg-disabled': ['background-color', '--disabled-bg'],
  'bg-switch-on': ['background-color', '--switch-on'],
  'bg-switch-thumb': ['background-color', '--switch-thumb'],
  'bg-overlay-backdrop': ['background-color', '--overlay-backdrop'],
}
for (const state of ['success', 'warning', 'danger', 'info']) {
  colorUtilities[`text-${state}`] = ['color', `--status-${state}`]
  colorUtilities[`bg-${state}-soft`] = ['background-color', `--status-${state}Bg`]
}
for (const category of ['ai', 'data', 'brand']) {
  colorUtilities[`text-${category}`] = ['color', `--accent-${category}`]
  colorUtilities[`bg-${category}-soft`] = ['background-color', `--accent-${category}Bg`]
}

const geometryUtilities: Record<string, [string, string]> = {
  'rounded-tag': ['border-radius', '--radius-sm'],
  'rounded-segment': ['border-radius', '--radius-segment'],
  'rounded-control': ['border-radius', '--radius-control'],
  'rounded-card': ['border-radius', '--radius-card'],
  'rounded-overlay': ['border-radius', '--radius-overlay'],
  'rounded-pill': ['border-radius', '--radius-pill'],
  'h-control-sm': ['height', '--control-sm'],
  'h-control': ['height', '--control-md'],
  'h-control-lg': ['height', '--control-lg'],
  'min-h-touch': ['min-height', '--control-touch'],
  'w-sidebar': ['width', '--layout-sidebar'],
  'h-topbar': ['height', '--layout-topbar'],
  'max-w-modal': ['max-width', '--overlay-modal'],
  'max-w-drawer': ['max-width', '--overlay-drawer'],
  'max-w-onboarding': ['max-width', '--overlay-onboarding'],
  'shadow-overlay': ['--tw-shadow', '--shadow-overlay'],
  'duration-fast': ['transition-duration', '--motion-fast'],
  'duration-overlay': ['transition-duration', '--motion-overlay'],
}
const typographyUtilities: Record<string, [string, string, string?]> = {
  'text-xs': ['0.75rem', '1rem'],
  'text-sm': ['0.875rem', '1.25rem'],
  'text-base': ['1rem', '1.5rem'],
  'text-3xl': ['1.875rem', '2.25rem'],
  'text-aux': ['12px', '16px'],
  'text-body': ['14px', '20px'],
  'text-title-card': ['16px', '24px', '600'],
  'text-title-section': ['18px', '28px', '600'],
  'text-title-page': ['24px', '32px', '700'],
}
// Default Tailwind values the dev styling relies on (v2 had remapped all of them).
const defaultUtilities: Record<string, [string, string]> = {
  'bg-gray-50': ['background-color', 'rgb(249 250 251 / var(--tw-bg-opacity, 1))'],
  'bg-zinc-100': ['background-color', 'rgb(244 244 245 / var(--tw-bg-opacity, 1))'],
  'bg-primary-600': ['background-color', 'rgb(225 29 72 / var(--tw-bg-opacity, 1))'],
  'rounded-md': ['border-radius', '0.375rem'],
  'rounded-2xl': ['border-radius', '1rem'],
}

const declarations = new Map<string, Map<string, string>>()
beforeAll(async () => {
  const classes = [
    ...Object.keys(colorUtilities),
    ...Object.keys(geometryUtilities),
    ...Object.keys(typographyUtilities),
    ...Object.keys(defaultUtilities),
    'font-sans', 'font-mono', 'transition-colors', 'shadow-sm',
  ]
  const result = await postcss([
    tailwindcss({
      ...tailwindConfig,
      content: [{ raw: classes.join(' '), extension: 'html' }],
    }),
  ]).process('@tailwind utilities;', { from: undefined })
  result.root.walkRules((rule) => {
    const properties = declarations.get(rule.selector) ?? new Map<string, string>()
    rule.walkDecls((declaration) => { properties.set(declaration.prop, declaration.value) })
    declarations.set(rule.selector, properties)
  })
})

function property(utility: string, name: string) {
  return declarations.get(`.${utility}`)?.get(name)
}

function lightToken(name: string) {
  let value: string | undefined
  postcss.parse(tokens).walkRules(':root', (rule) => {
    rule.walkDecls(name, (declaration) => { value = declaration.value })
  })
  return value
}

describe('Dev-based theme contract', () => {
  it('emits every semantic colour as its theme-aware token', () => {
    for (const [utility, [name, token]] of Object.entries(colorUtilities)) {
      expect(property(utility, name), utility).toBe(`var(${token})`)
    }
  })

  it('preserves control, overlay, layout and motion token mappings', () => {
    for (const [utility, [name, token]] of Object.entries(geometryUtilities)) {
      expect(property(utility, name), utility).toBe(`var(${token})`)
    }
    expect(property('transition-colors', 'transition-duration')).toBe('150ms')
    expect(property('transition-colors', 'transition-timing-function')).toBe('cubic-bezier(0.4, 0, 0.2, 1)')
  })

  it('keeps the overlay sizes the remaining token users depend on', () => {
    expect(lightToken('--radius-segment')).toBe('6px')
    expect(lightToken('--overlay-modal')).toBe('560px')
    expect(lightToken('--overlay-drawer')).toBe('480px')
    expect(lightToken('--overlay-onboarding')).toBe('768px')
  })

  it('keeps the Tailwind default type scale beside the semantic aliases', () => {
    for (const [utility, [size, lineHeight, weight]] of Object.entries(typographyUtilities)) {
      expect(property(utility, 'font-size'), utility).toBe(size)
      expect(property(utility, 'line-height'), utility).toBe(lineHeight)
      if (weight) expect(property(utility, 'font-weight'), utility).toBe(weight)
    }
  })

  it('uses the same font stacks for CSS baseline and utilities', () => {
    const normalize = (value: string) => value.replace(/["']/g, '').replace(/\s*,\s*/g, ',').trim()
    for (const family of ['sans', 'mono']) {
      const baseline = lightToken(`--font-${family}`)
      const utility = property(`font-${family}`, 'font-family')
      expect(baseline, `--font-${family}`).toBeTruthy()
      expect(utility, `font-${family}`).toBeTruthy()
      expect(normalize(utility!)).toBe(normalize(baseline!))
    }
  })

  it('does not remap the default palette, radii or small shadows', () => {
    for (const [utility, [name, value]] of Object.entries(defaultUtilities)) {
      expect(property(utility, name), utility).toBe(value)
    }
    expect(property('shadow-sm', '--tw-shadow')).not.toBe('none')
  })
})
