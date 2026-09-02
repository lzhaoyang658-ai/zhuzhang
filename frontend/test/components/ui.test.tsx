import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Money } from "@/components/ui";

describe("Money", () => {
  it.each([
    [123_456, false, "¥1,234.56"],
    [123_456, true, "+¥1,234.56"],
    [-123_456, false, "-¥1,234.56"],
    [5, false, "¥0.05"],
    [-5, true, "-¥0.05"],
    [100, true, "+¥1"],
    [0, true, "¥0"],
  ])("renders %i cents with sign=%s as %s", (cents, sign, expected) => {
    render(<Money cents={cents} sign={sign} />);
    expect(screen.getByText(expected)).toBeInTheDocument();
  });
});
