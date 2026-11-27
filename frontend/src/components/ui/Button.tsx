import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { buttonClasses, type ButtonSize, type ButtonVariant } from './buttonStyles'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  children: ReactNode
}

export default function Button({
  variant = 'primary', size = 'md', className = '', children, ...rest
}: ButtonProps) {
  return (
    <button
      className={buttonClasses(variant, size, className)}
      {...rest}
    >
      {children}
    </button>
  )
}
