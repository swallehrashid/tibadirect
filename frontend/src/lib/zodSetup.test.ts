import { z } from "zod";
import "./zodSetup";

describe("zod setup", () => {
  it("does not compile validators with eval, which the page's policy forbids", () => {
    expect(z.config().jitless).toBe(true);
  });

  it("still validates exactly the same", () => {
    const schema = z.object({ email: z.string().email(), n: z.number().int().min(1) });
    expect(schema.safeParse({ email: "a@b.co", n: 2 }).success).toBe(true);
    expect(schema.safeParse({ email: "nope", n: 0 }).success).toBe(false);
  });
});
