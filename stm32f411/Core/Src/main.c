/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body — atış kontrol (hss)
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "protocol.h"
#include "servo.h"
#include "trigger.h"
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define HEARTBEAT_TIMEOUT_MS  200u
#define TELEM_PERIOD_MS        50u
#define RX_RING_SIZE           64u
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
static volatile uint8_t  s_rx_ring[RX_RING_SIZE];
static volatile uint16_t s_rx_head = 0;
static volatile uint16_t s_rx_tail = 0;
uint8_t s_rx_byte = 0;

static uint8_t  s_frame_buf[PROTO_FRAME_LEN];
static uint8_t  s_frame_idx = 0;

static volatile uint32_t s_ms = 0;
static uint32_t s_last_cmd_ms = 0;
static uint32_t s_last_telem_ms = 0;

static bool     s_failsafe = true;
static bool     s_armed    = false;
static bool     s_enabled  = false;
static uint8_t  s_stage    = 0;
static bool     s_fire_edge_armed = false;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
static void rx_ring_push(uint8_t b);
static bool rx_ring_pop(uint8_t *b);
static void enter_failsafe(void);
static void send_telemetry(bool fired_event);
static void handle_command(const ProtoCommand *cmd);
static void poll_uart_frames(void);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void rx_ring_push(uint8_t b)
{
  uint16_t next = (uint16_t)((s_rx_head + 1u) % RX_RING_SIZE);
  if (next == s_rx_tail) {
    return;
  }
  s_rx_ring[s_rx_head] = b;
  s_rx_head = next;
}

static bool rx_ring_pop(uint8_t *b)
{
  if (s_rx_head == s_rx_tail) {
    return false;
  }
  *b = s_rx_ring[s_rx_tail];
  s_rx_tail = (uint16_t)((s_rx_tail + 1u) % RX_RING_SIZE);
  return true;
}

static void enter_failsafe(void)
{
  s_failsafe = true;
  s_armed    = false;
  Trigger_Abort();
  Servo_SetEnabled(false);
  Servo_Hold();
}

static void send_telemetry(bool fired_event)
{
  ProtoTelemetry tel;
  tel.pan_cdeg  = Servo_GetPanCdeg();
  tel.tilt_cdeg = Servo_GetTiltCdeg();
  tel.status    = 0;

  if (fired_event) {
    tel.status |= STATUS_FIRED;
  }
  if (s_armed) {
    tel.status |= STATUS_ARMED;
  }
  if (s_failsafe) {
    tel.status |= STATUS_FAILSAFE;
  }
  if (s_enabled && !s_failsafe) {
    tel.status |= STATUS_ENABLED;
  }
  if (Trigger_IsBusy()) {
    tel.status |= STATUS_BUSY;
  }
  if (Servo_WasLimited()) {
    tel.status |= STATUS_ANGLE_LIM;
  }

  uint8_t frame[PROTO_FRAME_LEN];
  Proto_BuildUplink(&tel, frame);
  HAL_UART_Transmit(&huart1, frame, PROTO_FRAME_LEN, 20);
}

static void handle_command(const ProtoCommand *cmd)
{
  s_last_cmd_ms = s_ms;
  s_stage = cmd->stage;

  if (cmd->flags & FLAG_SAFE) {
    enter_failsafe();
    return;
  }

  s_failsafe = false;

  if (cmd->flags & FLAG_HOME) {
    Servo_Home();
  }

  s_enabled = (cmd->flags & FLAG_ENABLE) != 0;
  Servo_SetEnabled(s_enabled && !s_failsafe);

  if (s_enabled) {
    Servo_SetAnglesCdeg(cmd->pan_cdeg, cmd->tilt_cdeg);
  }

  s_armed = (cmd->flags & FLAG_ARM) != 0;

  const bool fire_req = (cmd->flags & FLAG_FIRE) != 0;
  if (fire_req && !s_fire_edge_armed) {
    s_fire_edge_armed = true;
    const bool ok =
        s_armed &&
        s_enabled &&
        !s_failsafe &&
        !Servo_WasLimited() &&
        !Trigger_IsBusy();

    if (ok) {
      Trigger_RequestFire();
      send_telemetry(true);
    }
  }
  if (!fire_req) {
    s_fire_edge_armed = false;
  }
}

static void poll_uart_frames(void)
{
  uint8_t b;
  while (rx_ring_pop(&b)) {
    if (s_frame_idx == 0) {
      if (b != PROTO_SYNC_DOWN) {
        continue;
      }
      s_frame_buf[0] = b;
      s_frame_idx = 1;
      continue;
    }

    s_frame_buf[s_frame_idx++] = b;
    if (s_frame_idx < PROTO_FRAME_LEN) {
      continue;
    }

    s_frame_idx = 0;
    ProtoCommand cmd;
    if (Proto_ParseDownlink(s_frame_buf, &cmd)) {
      handle_command(&cmd);
    }
  }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART1) {
    rx_ring_push(s_rx_byte);
    HAL_UART_Receive_IT(&huart1, &s_rx_byte, 1);
  }
}

void HAL_SYSTICK_Callback(void)
{
  s_ms++;
  Trigger_Tick1ms();
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_TIM3_Init();
  MX_USART1_UART_Init();
  /* USER CODE BEGIN 2 */
  Trigger_Init();
  Servo_Init();
  enter_failsafe();
  HAL_UART_Receive_IT(&huart1, &s_rx_byte, 1);
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    poll_uart_frames();

    if (!s_failsafe && (s_ms - s_last_cmd_ms) > HEARTBEAT_TIMEOUT_MS) {
      enter_failsafe();
    }

    if ((s_ms - s_last_telem_ms) >= TELEM_PERIOD_MS) {
      s_last_telem_ms = s_ms;
      const bool fired = Trigger_ConsumeFiredFlag();
      send_telemetry(fired);
    }
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 8;
  RCC_OscInitStruct.PLL.PLLN = 84;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  (void)file;
  (void)line;
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
