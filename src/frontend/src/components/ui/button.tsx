import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/**
 * All button colors come from the `brand` / `brand-dark` CSS variables in index.css.
 * To restyle every button in the app, edit those variables there - not here.
 *
 * The `primary` variant pairs a normal filled button with its own outline, pushed
 * outward by `outline-offset` so there's a visible gap between the button and the
 * border around it.
 */
const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-DEFAULT text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-background',
  {
    variants: {
      variant: {
        // Plain `outline` gets silently dropped by tailwind-merge (it treats it as
        // conflicting with `outline-2`), so the style is set as an arbitrary property.
        primary:
          "bg-brand text-brand-foreground [outline-style:solid] outline-2 outline-offset-4 outline-brand hover:bg-brand-dark hover:outline-brand-dark active:bg-brand-dark active:outline-brand-dark",
        outline:
          'border border-brand text-brand hover:bg-brand hover:text-brand-foreground',
        ghost: 'text-foreground hover:bg-surface',
        destructive:
          'bg-destructive text-destructive-foreground hover:opacity-90',
      },
      size: {
        sm: 'h-8 px-3',
        md: 'h-10 px-4',
        lg: 'h-11 px-6 text-base',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'md',
    },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'
