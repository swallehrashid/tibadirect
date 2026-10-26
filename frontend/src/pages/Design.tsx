import { Bell, Info, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { usePublicConfig } from "@/api/queries";
import { BillMeter } from "@/components/BillMeter";
import { ConsentCard } from "@/components/ConsentCard";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { FileDropzone } from "@/components/FileDropzone";
import { LanguageToggle } from "@/components/LanguageToggle";
import { Logo } from "@/components/Logo";
import { MoneyInput } from "@/components/MoneyInput";
import { OtpInput } from "@/components/OtpInput";
import { RiskBand } from "@/components/RiskBand";
import { SandboxTag } from "@/components/SandboxTag";
import { Stepper } from "@/components/Stepper";
import { StatusTimeline } from "@/components/StatusTimeline";
import { TrustBadge } from "@/components/TrustBadge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/components/ui/toaster";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const SWATCHES: { name: string; token: string; role: string; dark?: boolean }[] = [
  { name: "Blue 900", token: "--tiba-blue-900", role: "Headers, footer, strong text", dark: true },
  { name: "Blue 700", token: "--tiba-blue-700", role: "Primary hover and pressed", dark: true },
  { name: "Tiba Blue 600", token: "--tiba-blue-600", role: "Primary: actions, links, settled bar", dark: true },
  { name: "Blue 100", token: "--tiba-blue-100", role: "Tinted surfaces" },
  { name: "Blue 50", token: "--tiba-blue-50", role: "Page wash" },
  { name: "Sky 500", token: "--sky-500", role: "Accent 1: pledged, information" },
  { name: "Uzima Teal 600", token: "--uzima-600", role: "Accent 2: hospital-confirmed, settled", dark: true },
  { name: "Jua Amber 400", token: "--jua-400", role: "Accent 3: pending, attention" },
  { name: "Danger 600", token: "--danger-600", role: "Errors only", dark: true },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <h2 className="font-heading text-2xl font-bold">{title}</h2>
      {children}
    </section>
  );
}

/** Design system showcase: every token and core component in one place. Dev and staging only. */
export function Design() {
  const { data: config, isPending } = usePublicConfig();
  const { t } = useTranslation();
  const [otp, setOtp] = useState("");
  const [money, setMoney] = useState<number | null>(2500);
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [step, setStep] = useState(1);

  if (isPending) return <Skeleton className="m-6 h-24" />;
  if (!import.meta.env.DEV && !config?.demo_banner) {
    return <EmptyState title="Not available" description="The design showcase is only available while the app runs in demo mode." />;
  }

  return (
    <div className="pb-16">
      <div className="container space-y-12 py-8">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1">
            <Logo />
            <h1 className="font-heading text-3xl font-extrabold">Design system</h1>
            <p className="text-muted-foreground">Blue means action, teal means confirmed, amber means pending, sky means pledged.</p>
          </div>
          <LanguageToggle />
        </header>

        <Section title="Colour">
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {SWATCHES.map((swatch) => (
              <li key={swatch.token} className="flex items-center gap-3 rounded-lg border border-border bg-card p-3">
                <span className="size-12 shrink-0 rounded-md border border-border" style={{ background: `var(${swatch.token})` }} aria-hidden="true" />
                <span>
                  <span className="block text-sm font-semibold">{swatch.name}</span>
                  <span className="block text-xs text-muted-foreground">{swatch.role}</span>
                </span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Type">
          <Card>
            <CardContent className="space-y-2">
              <p className="font-heading text-4xl font-extrabold">Plus Jakarta Sans for headings</p>
              <p>Inter for body text, 16 px on a 1.5 line height, so a bill amount like KES 128,400 stays easy to read.</p>
              <p className="text-sm text-muted-foreground">Muted text uses slate 600 and passes AA on every surface.</p>
            </CardContent>
          </Card>
        </Section>

        <Section title="Buttons and chips">
          <div className="flex flex-wrap gap-3">
            <Button>Donate</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="link">Link</Button>
            <Button variant="destructive">Freeze case</Button>
            <Button disabled>Disabled</Button>
            <Button size="sm">Small</Button>
            <Button size="icon" aria-label="Notifications">
              <Bell />
            </Button>
          </div>
          <div className="flex flex-wrap items-start gap-4">
            <TrustBadge level="hospital_confirmed" />
            <TrustBadge level="pending_confirmation" />
            <TrustBadge level="unverified" />
            <RiskBand band="low" />
            <RiskBand band="medium" />
            <RiskBand band="high" />
            <Badge tone="info">Pledged</Badge>
            <Badge tone="brand">Brand</Badge>
            <SandboxTag />
          </div>
          <TrustBadge level="pending_confirmation" explain />
        </Section>

        <Section title="Bill meter">
          <div className="grid gap-4 md:grid-cols-3">
            {[
              { title: "Live", props: { targetMinor: 12_840_000, pledgedMinor: 7_704_000, settledMinor: 3_852_000, usdGoalMinor: 100_000 } },
              { title: "Estimate, no money yet", props: { targetMinor: 5_000_000, pledgedMinor: 0, settledMinor: 0, isEstimate: true } },
              { title: "Not confirmed", props: { targetMinor: null, pledgedMinor: 0, settledMinor: 0 } },
            ].map((item) => (
              <Card key={item.title}>
                <CardHeader>
                  <CardTitle>{item.title}</CardTitle>
                </CardHeader>
                <CardContent>
                  <BillMeter {...item.props} />
                </CardContent>
              </Card>
            ))}
          </div>
        </Section>

        <Section title="Forms">
          <div className="grid gap-6 md:grid-cols-2">
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="d-email">Email</Label>
                <Input id="d-email" type="email" placeholder="you@example.com" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="d-amount">Donation amount</Label>
                <MoneyInput id="d-amount" value={money} onChange={setMoney} />
                <p className="text-sm text-muted-foreground">Minor units: {money ?? "none"}</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="d-error">With an error</Label>
                <Input id="d-error" aria-invalid="true" aria-describedby="d-error-msg" defaultValue="abc" />
                <p id="d-error-msg" className="text-sm font-semibold text-danger-700">Enter a valid email address.</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="d-story">Story</Label>
                <Textarea id="d-story" placeholder="Tell donors about your family, not the diagnosis." />
              </div>
              <div className="space-y-2">
                <Label htmlFor="d-county">County</Label>
                <Select>
                  <SelectTrigger id="d-county">
                    <SelectValue placeholder="Choose a county" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="nairobi">Nairobi</SelectItem>
                    <SelectItem value="mombasa">Mombasa</SelectItem>
                    <SelectItem value="kisumu">Kisumu</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-4">
              <OtpInput value={otp} onChange={setOtp} />
              <RadioGroup defaultValue="refund" aria-label="What to do with extra money">
                {["refund", "solidarity_pool", "follow_up_care"].map((value) => (
                  <div key={value} className="flex items-center gap-3">
                    <RadioGroupItem value={value} id={`r-${value}`} />
                    <Label htmlFor={`r-${value}`}>{value.replace(/_/g, " ")}</Label>
                  </div>
                ))}
              </RadioGroup>
              <div className="flex items-center gap-3">
                <Checkbox id="d-anon" />
                <Label htmlFor="d-anon">Give anonymously</Label>
              </div>
              <div className="flex items-center gap-3">
                <Switch id="d-photo" aria-label="Show a photo" />
                <Label htmlFor="d-photo">Show a photo</Label>
              </div>
            </div>
          </div>
          <FileDropzone file={file} onChange={setFile} label="Hospital bill" />
          <ConsentCard
            title="Hospital check"
            text={"We will send the bill details to the hospital's billing office so it can confirm the amount.\nWe never share your contact details."}
            required
            checked={consent}
            onCheckedChange={setConsent}
          />
        </Section>

        <Section title="Progress">
          <Stepper steps={["Hospital", "Consent", "Bill", "Page", "Review"]} current={step} />
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setStep((s) => Math.max(0, s - 1))}>
              {t("common.back")}
            </Button>
            <Button size="sm" onClick={() => setStep((s) => Math.min(4, s + 1))}>
              {t("common.next")}
            </Button>
          </div>
          <Card>
            <CardContent>
              <StatusTimeline
                steps={[
                  { key: "a", label: "Bill uploaded", at: "2026-10-20T08:10:00Z", state: "done" },
                  { key: "b", label: "Checked", at: "2026-10-20T08:11:00Z", state: "done" },
                  { key: "c", label: "Waiting for the hospital", state: "current" },
                  { key: "d", label: "Live on TibaDirect", state: "upcoming" },
                ]}
              />
            </CardContent>
          </Card>
        </Section>

        <Section title="Feedback">
          <div className="grid gap-3 md:grid-cols-2">
            <Alert tone="info"><Info /><div><AlertTitle>Information</AlertTitle><AlertDescription>Pledged money is held until the hospital confirms.</AlertDescription></div></Alert>
            <Alert tone="success"><Info /><div><AlertTitle>Confirmed</AlertTitle><AlertDescription>The hospital confirmed this bill.</AlertDescription></div></Alert>
            <Alert tone="warning"><Info /><div><AlertTitle>Pending</AlertTitle><AlertDescription>We are waiting for the hospital.</AlertDescription></div></Alert>
            <Alert tone="danger"><Info /><div><AlertTitle>Error</AlertTitle><AlertDescription>That code is not right.</AlertDescription></div></Alert>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <EmptyState description="When a family shares a case it will show up here." />
            <ErrorState error={new ApiError(500, { code: "internal_error", message: "x", request_id: "req-demo-1" })} onRetry={() => toast.success("Retrying")} />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={() => toast.success("Saved", { description: "Your changes are saved." })}>Success toast</Button>
            <Button variant="outline" onClick={() => toast.error("Could not save", { description: "Please try again." })}>Error toast</Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="About tooltips"><Info /></Button>
              </TooltipTrigger>
              <TooltipContent>Tooltips are extras; essential words are always on the page.</TooltipContent>
            </Tooltip>
            <Dialog>
              <DialogTrigger asChild><Button variant="outline">Open dialog</Button></DialogTrigger>
              <DialogContent>
                <DialogHeader><DialogTitle>Dialog</DialogTitle><DialogDescription>Focus stays inside until it is closed.</DialogDescription></DialogHeader>
                <DialogFooter><Button>Done</Button></DialogFooter>
              </DialogContent>
            </Dialog>
            <AlertDialog>
              <AlertDialogTrigger asChild><Button variant="destructive"><Trash2 /> Freeze case</Button></AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader><AlertDialogTitle>Freeze this case?</AlertDialogTitle><AlertDialogDescription>Donations stop and open donors are refunded.</AlertDialogDescription></AlertDialogHeader>
                <AlertDialogFooter><AlertDialogCancel>Cancel</AlertDialogCancel><AlertDialogAction destructive>Freeze</AlertDialogAction></AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
          <div className="space-y-2">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-24 w-full" />
          </div>
        </Section>

        <Section title="Tabs and separators">
          <Tabs defaultValue="one">
            <TabsList>
              <TabsTrigger value="one">Story</TabsTrigger>
              <TabsTrigger value="two">Ledger</TabsTrigger>
            </TabsList>
            <TabsContent value="one"><Card><CardHeader><CardTitle>Story</CardTitle><CardDescription>No diagnosis is ever shown.</CardDescription></CardHeader></Card></TabsContent>
            <TabsContent value="two"><Card><CardContent>Public ledger rows go here.</CardContent></Card></TabsContent>
          </Tabs>
          <Separator />
        </Section>
      </div>
    </div>
  );
}

export const Component = Design;
