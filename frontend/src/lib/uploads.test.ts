import { MAX_UPLOAD_BYTES, validateUpload } from "./uploads";

const file = (name: string, type: string, size = 1000) => ({ name, type, size });

describe("validateUpload", () => {
  it("accepts a PDF, PNG and JPEG", () => {
    expect(validateUpload(file("bill.pdf", "application/pdf"))).toBeNull();
    expect(validateUpload(file("bill.PNG", "image/png"))).toBeNull();
    expect(validateUpload(file("bill.jpg", "image/jpeg"))).toBeNull();
    expect(validateUpload(file("bill.jpeg", "image/jpeg"))).toBeNull();
  });

  it("accepts a file whose type the phone left blank when the name is right", () => {
    expect(validateUpload(file("scan.jpg", ""))).toBeNull();
  });

  it("rejects other types and mismatched names", () => {
    expect(validateUpload(file("bill.docx", "application/vnd.openxmlformats"))).toBe("type");
    expect(validateUpload(file("bill.pdf", "image/png"))).toBe("type");
    expect(validateUpload(file("bill", "application/pdf"))).toBe("type");
    expect(validateUpload(file("bill.svg", "image/svg+xml"))).toBe("type");
  });

  it("rejects empty and oversized files", () => {
    expect(validateUpload(file("bill.pdf", "application/pdf", 0))).toBe("empty");
    expect(validateUpload(file("bill.pdf", "application/pdf", MAX_UPLOAD_BYTES))).toBeNull();
    expect(validateUpload(file("bill.pdf", "application/pdf", MAX_UPLOAD_BYTES + 1))).toBe("size");
  });
});
