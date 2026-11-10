const appPort = process.env.SAVVA_APP_PORT || "8769";
const debugPort = process.env.SAVVA_DEBUG_PORT || "9223";
const projectName = process.env.SAVVA_E2E_PROJECT || "savva-ui-demo";

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function connectTarget() {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json`);
  const targets = await response.json();
  const target = targets.find(
    (item) => item.type === "page" && item.url.includes(`127.0.0.1:${appPort}`),
  );
  if (!target) throw new Error("SAVVA page target was not found.");

  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  return { socket, send };
}

async function main() {
  const { socket, send } = await connectTarget();
  await send("Runtime.enable");

  const evaluate = async (expression) => {
    const result = await send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "Browser evaluation failed.");
    }
    return result.result.value;
  };

  const waitFor = async (expression, timeoutMilliseconds = 120_000) => {
    const deadline = Date.now() + timeoutMilliseconds;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await sleep(350);
    }
    throw new Error(`Timed out waiting for: ${expression}`);
  };

  try {
    await waitFor("document.querySelector('#tool-count')?.textContent !== '0'");
    const prompt =
      `Создай сайт для продажи компьютеров SAVVA на React, TypeScript и Vite. ` +
      `Используй имя проекта ${projectName}`;
    await evaluate(`(() => {
      const input = document.querySelector("#message-input");
      input.value = ${JSON.stringify(prompt)};
      input.dispatchEvent(new Event("input", { bubbles: true }));
      document.querySelector("#composer").requestSubmit();
      return true;
    })()`);

    await waitFor(
      "document.querySelector('.proposal-card button, .confirmation-card button') !== null",
    );
    const proposalSummary = await evaluate(
      "document.querySelector('.proposal-card, .confirmation-card').innerText",
    );
    await evaluate(
      "document.querySelector('.proposal-card button, .confirmation-card button').click()",
    );
    await waitFor("document.querySelector('#proposal-dialog').open === true");
    const diff = await evaluate("document.querySelector('#proposal-diff').textContent");
    const changes = await evaluate(
      "[...document.querySelectorAll('.change-chip')].map((item) => item.textContent)",
    );

    await evaluate("document.querySelector('#approve-proposal').click()");
    await waitFor(
      "document.querySelector('#proposal-dialog').open === false && " +
        "document.body.innerText.includes('Files written after exact proposal approval')",
    );
    await evaluate("document.querySelector('#tab-projects').click()");
    await waitFor(
      `document.querySelector('#project-tree').innerText.includes(${JSON.stringify(projectName)})`,
    );
    const treeText = await evaluate("document.querySelector('#project-tree').innerText");
    const journalText = await evaluate("document.querySelector('#action-journal').innerText");

    console.log(
      JSON.stringify(
        {
          status: "success",
          project: projectName,
          proposal: proposalSummary,
          diff_chars: diff.length,
          changes,
          tree_has_project: treeText.includes(projectName),
          journal_has_apply: journalText.includes("apply"),
        },
        null,
        2,
      ),
    );
  } finally {
    await send("Browser.close").catch(() => {});
    socket.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
