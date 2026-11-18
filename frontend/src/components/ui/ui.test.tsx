import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ConfirmModal,
  SkeletonCard,
  StatusBadge,
  ToastProvider,
  useToast,
} from "./index";

function ToastHarness() {
  const { toast } = useToast();
  return (
    <Button
      onClick={() =>
        toast({
          title: "Saved clip plan",
          description: "Template applied",
          tone: "ok",
        })
      }
    >
      Trigger Toast
    </Button>
  );
}

describe("Design System UI Primitives", () => {
  it("enforces >= 44px touch target classes across all Button sizes and variants", () => {
    render(
      <div>
        <Button size="sm" variant="primary">
          Small
        </Button>
        <Button size="md" variant="secondary">
          Medium
        </Button>
        <Button size="lg" variant="destructive">
          Large
        </Button>
        <Button variant="ghost" loading>
          Loading
        </Button>
      </div>
    );

    const sm = screen.getByRole("button", { name: "Small" });
    const md = screen.getByRole("button", { name: "Medium" });
    const lg = screen.getByRole("button", { name: "Large" });
    const loadingBtn = screen.getByRole("button", { name: "Loading" });

    expect(sm.className).toContain("min-h-[44px]");
    expect(sm.className).toContain("min-w-[44px]");
    expect(md.className).toContain("min-h-[44px]");
    expect(lg.className).toContain("min-h-[48px]");
    expect(loadingBtn).toBeDisabled();
    expect(loadingBtn).toHaveAttribute("aria-busy", "true");
  });

  it("renders Card, Badge, StatusBadge, and SkeletonCard with semantic classes", () => {
    const { container } = render(
      <div>
        <Card selected>
          <CardHeader>
            <CardTitle>Viral Clip</CardTitle>
            <StatusBadge status="COMPLETED" />
          </CardHeader>
          <CardDescription>Strong hook in first 2s</CardDescription>
          <CardContent>
            <Badge tone="route">source</Badge>
          </CardContent>
        </Card>
        <SkeletonCard />
      </div>
    );

    expect(screen.getByText("Viral Clip")).toBeInTheDocument();
    expect(screen.getByText("COMPLETED")).toHaveClass("text-ok");
    expect(screen.getByText("source")).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"]')).toBeInTheDocument();
  });

  it("displays and dismisses Toast notifications", () => {
    render(
      <ToastProvider>
        <ToastHarness />
      </ToastProvider>
    );

    fireEvent.click(screen.getByRole("button", { name: "Trigger Toast" }));
    expect(screen.getByRole("status")).toHaveTextContent("Saved clip plan");

    const closeBtn = screen.getByRole("button", {
      name: /Dismiss notification/i,
    });
    expect(closeBtn.className).toContain("min-h-[44px]");
    fireEvent.click(closeBtn);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders ConfirmModal with consequence text and handles confirm/cancel/Escape", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();

    render(
      <ConfirmModal
        open
        title="Delete job?"
        description="This permanently deletes all rendered MP4 files."
        confirmLabel="Delete Permanently"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByText(/permanently deletes all rendered MP4 files/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete Permanently" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
