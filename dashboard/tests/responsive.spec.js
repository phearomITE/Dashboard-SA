import {test,expect} from '@playwright/test'

for(const route of ['overview','live-map','missing-report','roster','annual-leave','settings']){
  test(`${route} fits the viewport`,async({page})=>{
    await page.goto('/#'+route)
    await expect(page.locator('h1')).toBeVisible()
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2)).toBe(true)
  })
}

test('navigation and member form work on narrow screens',async({page})=>{
  await page.goto('/#roster')
  if(await page.getByRole('button',{name:'Open navigation'}).isVisible()){
    await page.getByRole('button',{name:'Open navigation'}).click()
    await expect(page.getByRole('navigation',{name:'Workspace'})).toBeVisible()
    await page.getByRole('complementary').getByRole('button',{name:'Close navigation'}).click()
  }
  await page.getByRole('button',{name:'Add member'}).click()
  await expect(page.getByRole('dialog',{name:'Add member'})).toBeVisible()
  await expect(page.getByRole('dialog').getByRole('combobox',{name:'Controller'})).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog',{name:'Add member'})).toHaveCount(0)
})
