import { useState } from 'react'

const EMPTY = {
  url:      '',
  product:  '',
  maxPrice: '',
  email:    '',
  password: '',
}

const PRESETS = [
  { label: 'Sauce Demo',    url: 'https://www.saucedemo.com', product: 'Sauce Labs Backpack', maxPrice: '35' },
  { label: 'Sauce — Bike', url: 'https://www.saucedemo.com', product: 'Sauce Labs Bike Light', maxPrice: '15' },
]

function validate(fields) {
  const errs = {}
  if (!fields.url.trim())                         errs.url      = 'URL is required'
  else if (!/^https?:\/\/.+/.test(fields.url))    errs.url      = 'Must start with http(s)://'
  if (!fields.product.trim())                     errs.product  = 'Product term is required'
  if (!fields.maxPrice || isNaN(Number(fields.maxPrice)) || Number(fields.maxPrice) <= 0)
                                                  errs.maxPrice = 'Enter a valid price'
  if (!fields.email.trim())                       errs.email    = 'Email is required'
  if (!fields.password.trim())                    errs.password = 'Password is required'
  return errs
}

export default function PurchaseForm({ onSubmit, isRunning }) {
  const [fields, setFields]   = useState(EMPTY)
  const [errors, setErrors]   = useState({})
  const [touched, setTouched] = useState({})

  function set(key, val) {
    setFields(f => ({ ...f, [key]: val }))
    if (touched[key]) {
      // re-validate only touched field inline
      setErrors(e => {
        const next = { ...e }
        if (val.trim()) delete next[key]
        return next
      })
    }
  }

  function blur(key) {
    setTouched(t => ({ ...t, [key]: true }))
    const errs = validate({ ...fields })
    setErrors(e => ({ ...e, [key]: errs[key] }))
  }

  function applyPreset(p) {
    setFields(f => ({ ...f, url: p.url, product: p.product, maxPrice: p.maxPrice }))
    setErrors({})
  }

  function handleSubmit(e) {
    e.preventDefault()
    const errs = validate(fields)
    if (Object.keys(errs).length) {
      setErrors(errs)
      setTouched({ url: true, product: true, maxPrice: true, email: true, password: true })
      return
    }
    onSubmit({
      url:      fields.url.trim(),
      product:  fields.product.trim(),
      maxPrice: parseFloat(fields.maxPrice),
      email:    fields.email.trim(),
      password: fields.password,
    })
  }

  function reset() {
    setFields(EMPTY)
    setErrors({})
    setTouched({})
  }

  return (
    <div className="card">
      <p className="card-title">New Purchase Job</p>

      {/* Quick-fill presets */}
      <div style={{ display: 'flex', gap: '.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        {PRESETS.map(p => (
          <button
            key={p.label}
            type="button"
            className="btn btn-ghost"
            style={{ fontSize: '.75rem', padding: '.35rem .8rem' }}
            onClick={() => applyPreset(p)}
            disabled={isRunning}
          >
            {p.label}
          </button>
        ))}
        <span style={{ fontSize: '.72rem', color: 'var(--muted)', alignSelf: 'center', marginLeft: '.25rem' }}>
          quick-fill
        </span>
      </div>

      <form onSubmit={handleSubmit} noValidate>
        <div className="form-grid">

          {/* Target URL */}
          <div className="field span-2">
            <label htmlFor="url">Target URL <span style={{ color: 'var(--error)' }}>*</span></label>
            <input
              id="url"
              type="url"
              placeholder="https://www.saucedemo.com"
              value={fields.url}
              onChange={e => set('url', e.target.value)}
              onBlur={() => blur('url')}
              className={errors.url ? 'error-input' : ''}
              disabled={isRunning}
              autoComplete="off"
            />
            {errors.url && <span className="field-error">{errors.url}</span>}
          </div>

          {/* Product */}
          <div className="field">
            <label htmlFor="product">Product / Search Term <span style={{ color: 'var(--error)' }}>*</span></label>
            <input
              id="product"
              type="text"
              placeholder="Sauce Labs Backpack"
              value={fields.product}
              onChange={e => set('product', e.target.value)}
              onBlur={() => blur('product')}
              className={errors.product ? 'error-input' : ''}
              disabled={isRunning}
            />
            {errors.product && <span className="field-error">{errors.product}</span>}
          </div>

          {/* Max price */}
          <div className="field">
            <label htmlFor="maxPrice">Max Price (USD) <span style={{ color: 'var(--error)' }}>*</span></label>
            <input
              id="maxPrice"
              type="number"
              min="0.01"
              step="0.01"
              placeholder="35.00"
              value={fields.maxPrice}
              onChange={e => set('maxPrice', e.target.value)}
              onBlur={() => blur('maxPrice')}
              className={errors.maxPrice ? 'error-input' : ''}
              disabled={isRunning}
            />
            {errors.maxPrice && <span className="field-error">{errors.maxPrice}</span>}
          </div>

          {/* Email */}
          <div className="field">
            <label htmlFor="email">Test Email <span style={{ color: 'var(--error)' }}>*</span></label>
            <input
              id="email"
              type="email"
              placeholder="test@blitzbuy.dev"
              value={fields.email}
              onChange={e => set('email', e.target.value)}
              onBlur={() => blur('email')}
              className={errors.email ? 'error-input' : ''}
              disabled={isRunning}
              autoComplete="off"
            />
            {errors.email && <span className="field-error">{errors.email}</span>}
          </div>

          {/* Password */}
          <div className="field">
            <label htmlFor="password">Test Password <span style={{ color: 'var(--error)' }}>*</span></label>
            <input
              id="password"
              type="password"
              placeholder="secret_sauce"
              value={fields.password}
              onChange={e => set('password', e.target.value)}
              onBlur={() => blur('password')}
              className={errors.password ? 'error-input' : ''}
              disabled={isRunning}
            />
            {errors.password && <span className="field-error">{errors.password}</span>}
          </div>

        </div>

        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={reset} disabled={isRunning}>
            Clear
          </button>
          <button type="submit" className="btn btn-primary" disabled={isRunning}>
            {isRunning ? (
              <><div className="spinner" /> Running…</>
            ) : (
              <>&#9654; Run Purchase</>
            )}
          </button>
        </div>
      </form>
    </div>
  )
}
