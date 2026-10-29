import "./globals.css";
export const metadata = {
  title: "Fieldwork · Job market intelligence",
  description: "Auditable observations of your job market.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
