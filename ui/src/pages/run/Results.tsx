import { VerdictBanner } from "../../components/VerdictBanner";
import { useRunIdx } from "./RunLayout";

export function Results() {
  const idx = useRunIdx();
  return (
    <div className="body">
      <main className="work">
        <VerdictBanner idx={idx} />
      </main>
    </div>
  );
}
