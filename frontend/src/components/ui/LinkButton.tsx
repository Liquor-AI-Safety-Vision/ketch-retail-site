import type { AnchorHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { buttonClasses, type ButtonSize, type ButtonVariant } from './buttonStyles'

type LinkButtonProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & {
  /** Internal hash-router path, e.g. "/dashboard". */
  to?: string
  /** Raw anchor href, e.g. an in-page "#section" anchor. */
  href?: string
  variant?: ButtonVariant
  size?: ButtonSize
  children: ReactNode
}

/* Link-styled CTA: identical styling to Button but one semantic <a> element,
   so an anchor never wraps a <button> (nested interactive controls). */
export default function LinkButton({
  to, href, variant = 'primary', size = 'md', className = '', children, ...rest
}: LinkButtonProps) {
  const classes = buttonClasses(variant, size, className)
  if (to !== undefined) {
    return (
      <Link to={to} className={classes} {...rest}>
        {children}
      </Link>
    )
  }
  return (
    <a href={href} className={classes} {...rest}>
      {children}
    </a>
  )
}
