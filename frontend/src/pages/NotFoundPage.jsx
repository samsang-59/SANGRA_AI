import { Link } from "react-router-dom";
import "./NotFoundPage.css";

export default function NotFoundPage() {
  return (
    <main className="not-found">
      <p className="not-found-code" aria-hidden="true">
        404<span className="not-found-cursor">&#9608;</span>
      </p>
      <p className="not-found-shell mono">$ cd /this-page &rarr; no such file or directory</p>
      <h1>Page not found</h1>
      <Link className="button" to="/projects">
        go to projects
      </Link>
    </main>
  );
}
