import { Link } from "react-router-dom";
import { Shell } from "../components/Shell";

export function NotFound() {
  return (
    <Shell>
      <div className="body">
        <main className="work">
          <h1>Not found</h1>
          <p className="lede">
            Nothing lives at this address. <Link to="/bench">Go to Bench</Link>.
          </p>
        </main>
      </div>
    </Shell>
  );
}
