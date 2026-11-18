import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export type CardVariant = "default" | "raised" | "dashed" | "interactive";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
  selected?: boolean;
}

const VARIANT_CLASSES: Record<CardVariant, string> = {
  default: "bg-surface border border-border",
  raised: "bg-surface-2 border border-border",
  dashed: "bg-surface border border-dashed border-border",
  interactive:
    "bg-surface/80 border border-border transition-colors duration-150 ease-out hover:border-text-dim",
};

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ variant = "default", selected = false, className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-lg p-4 text-text",
        VARIANT_CLASSES[variant],
        selected && "border-accent bg-surface",
        className
      )}
      {...props}
    />
  )
);

Card.displayName = "Card";

export const CardHeader = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("flex flex-wrap items-start justify-between gap-2", className)}
    {...props}
  />
));

CardHeader.displayName = "CardHeader";

export const CardTitle = forwardRef<
  HTMLHeadingElement,
  HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn("text-sm font-bold leading-snug text-text", className)}
    {...props}
  />
));

CardTitle.displayName = "CardTitle";

export const CardDescription = forwardRef<
  HTMLParagraphElement,
  HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn("text-xs text-text-dim leading-normal", className)}
    {...props}
  />
));

CardDescription.displayName = "CardDescription";

export const CardContent = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("mt-3", className)} {...props} />
));

CardContent.displayName = "CardContent";

export const CardFooter = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("mt-4 flex flex-wrap items-center justify-end gap-2", className)}
    {...props}
  />
));

CardFooter.displayName = "CardFooter";
