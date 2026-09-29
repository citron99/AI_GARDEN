const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;


async function assertNoSeriousAccessibilityViolations(page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const blocking = results.violations.filter((item) => ["critical", "serious"].includes(item.impact));
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
}

async function assertNoHorizontalOverflow(page) {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}


test("gardener completes diagnosis, answers, reminder, and product journey", async ({ page }) => {
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.route(/\/api\/v1\/gardens\/\d+\/weather$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        provider: "E2E weather",
        source_url: "https://open-meteo.com/",
        location: "E2E location",
        country_code: "LV",
        latitude: 56.95,
        longitude: 24.1,
        timezone: "Europe/Riga",
        fetched_at: "2026-07-15T12:00:00Z",
        daily: [],
        warnings: [],
      },
    });
  });
  await page.route(/\/api\/v1\/recommendations\/\d+\/products$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: [{
        id: 9001,
        name: "Soil moisture meter",
        sku: "E2E-METER-1",
        category: "diagnostic_tool",
        description: "Low-risk diagnostic product",
        product_url: "https://partner.example/meter",
        image_url: null,
        price_cents: 1299,
        currency: "eur",
        in_stock: true,
        regions: ["LV"],
        partner_name: "E2E Garden Shop",
        commercial_label: "Реклама партнёра",
        moderation_status: "approved",
      }],
    });
  });
  await page.goto("/");
  await page.locator("#languageSelect").selectOption("ru");
  await expect(page).toHaveTitle(/AI Garden/);
  await assertNoSeriousAccessibilityViolations(page);
  await assertNoHorizontalOverflow(page);

  await page.locator("#loginTab").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("#registerTab")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#registerForm")).toBeVisible();
  await page.locator("#registerForm input[name='name']").fill("E2E Gardener");
  await page.locator("#registerForm input[name='email']").fill(`e2e-${Date.now()}@example.test`);
  await page.locator("#registerForm input[name='password']").fill("e2e-strong-password");
  await page.locator("#registerForm input[name='region']").fill("");
  await page.locator("#registerForm button[type='submit']").click();
  await expect(page.locator("#appView")).toBeVisible();
  await expect(page.locator("#profileName")).toHaveText("E2E Gardener");

  await page.locator("#showPlantForm").click();
  await expect(page.locator("#toast")).toContainText(
    "Сначала создайте сад. После сохранения откроется форма растения.",
  );
  await expect(page.locator("#gardenForm")).toBeVisible();
  await expect(page.locator("#gardenForm input[name='name']")).toBeFocused();
  await page.locator("#gardenForm input[name='name']").fill("E2E Garden");
  await page.locator("#gardenForm button[type='submit']").click();
  await expect(page.locator("#gardenList")).toContainText("E2E Garden");
  await expect(page.locator("#plantForm")).toBeVisible();
  await expect(page.locator("#plantForm input[name='name']")).toBeFocused();
  await page.locator("#plantForm input[name='name']").fill("E2E Tomato");
  await page.locator("#plantForm input[name='species']").fill("Solanum lycopersicum");
  await page.locator("#plantForm input[name='taxon_id']").fill("solanum.lycopersicum");
  await page.locator("#plantForm select[name='growing_place']").selectOption("open_ground");
  await page.locator("#plantForm button[type='submit']").click();
  await expect(page.locator("#plantTitle")).toHaveText("E2E Tomato");
  await assertNoHorizontalOverflow(page);

  await page.locator("#dashboardSettingsButton").click();
  await expect(page.locator("#dashboardSettingsModal")).toBeVisible();
  await page.getByLabel("Погода и предупреждения").uncheck();
  await expect(page.locator("#weatherPanel")).toBeHidden();
  await page.getByRole("button", { name: "Готово" }).click();
  await page.reload();
  await expect(page.locator("#appView")).toBeVisible();
  await page.getByRole("button", { name: /E2E Tomato/ }).click();
  await expect(page.locator("#weatherPanel")).toBeHidden();
  await page.locator("#dashboardSettingsButton").click();
  await page.getByRole("button", { name: "Показать все" }).click();
  await page.getByRole("button", { name: "Готово" }).click();
  await expect(page.locator("#weatherPanel")).toBeVisible();

  const pngBase64 = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 128;
    canvas.height = 128;
    const context = canvas.getContext("2d");
    context.fillStyle = "#3f7d4d";
    context.fillRect(0, 0, 128, 128);
    context.fillStyle = "#e8d65c";
    context.beginPath();
    context.ellipse(64, 64, 25, 50, Math.PI / 4, 0, Math.PI * 2);
    context.fill();
    return canvas.toDataURL("image/png").split(",")[1];
  });
  await page.locator("#photoInput").setInputFiles({
    name: "e2e-leaf.png",
    mimeType: "image/png",
    buffer: Buffer.from(pngBase64, "base64"),
  });
  await page.locator("#diagnosisForm textarea[name='symptoms']").fill(
    "Нижние листья начали желтеть неделю назад",
  );
  await page.locator("#diagnoseButton").click();
  await expect(page.locator("#diagnosisMessage")).toHaveText("Анализ завершён", { timeout: 20_000 });
  await expect(page.locator("#resultPanel")).toContainText("Нарушение режима полива");
  await expect(page.locator("#resultPanel")).toContainText("Soil moisture meter");

  const answerFields = page.locator("#resultPanel textarea[data-question-id]");
  await expect(answerFields).toHaveCount(3);
  const answers = ["Старые листья", "Пятен снизу нет", "Полив стал чаще"];
  for (let index = 0; index < answers.length; index += 1) {
    await answerFields.nth(index).fill(answers[index]);
  }
  await page.getByRole("button", { name: "Ответить и пересмотреть" }).click();
  await expect(page.locator("#historyList")).toContainText("Старые листья", { timeout: 20_000 });

  const feedbackForm = page.locator("#resultPanel form").filter({
    has: page.getByRole("heading", { name: "Оценить результат" }),
  });
  await feedbackForm.locator("select").selectOption("5");
  await feedbackForm.locator("textarea").fill("Полезный предварительный результат");
  await feedbackForm.getByRole("button", { name: "Сохранить отзыв" }).click();
  await expect(page.locator("#toast")).toContainText("Спасибо, отзыв сохранён");

  await page.locator("#reminderForm input[name='title']").fill("Проверить листья томата");
  await page.locator("#reminderForm select[name='recurrence']").selectOption("monthly");
  await page.locator("#reminderForm select[name='preferred_channel']").selectOption("both");
  await page.locator("#reminderForm button[type='submit']").click();
  await expect(page.locator("#reminderList")).toContainText("Проверить листья томата");
  await expect(page.locator("#reminderList")).toContainText("ежемесячно");

  await assertNoHorizontalOverflow(page);
  await assertNoSeriousAccessibilityViolations(page);
  expect(consoleErrors).toEqual([]);
});
