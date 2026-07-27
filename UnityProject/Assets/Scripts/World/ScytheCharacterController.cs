using SCYTHE.Core;
using UnityEngine;

namespace SCYTHE.World
{
    [RequireComponent(typeof(CharacterController))]
    public sealed class ScytheCharacterController : MonoBehaviour
    {
        [SerializeField] private Camera viewCamera;
        [SerializeField, Min(0.1f)] private float walkSpeedMetersPerSecond = 3.5f;
        [SerializeField, Min(0.1f)] private float sprintSpeedMetersPerSecond = 6f;
        [SerializeField, Min(0f)] private float jumpHeightMeters = 1f;
        [SerializeField, Min(0.1f)] private float gravityMetersPerSecondSquared = 9.80665f;
        [SerializeField, Min(1f)] private float mouseSensitivity = 120f;

        private CharacterController character;
        private Vector2 moveInput;
        private float verticalVelocity;
        private float cameraPitch;
        private bool sprintRequested;
        private bool jumpRequested;

        public Vector3 Velocity => character == null ? Vector3.zero : character.velocity;
        public bool PointerCaptured => Cursor.lockState == CursorLockMode.Locked;

        public void BindCamera(Camera camera)
        {
            viewCamera = camera;
        }

        public void Configure(float walkSpeed, float sprintSpeed)
        {
            walkSpeedMetersPerSecond = Mathf.Max(0.1f, walkSpeed);
            sprintSpeedMetersPerSecond = Mathf.Max(walkSpeedMetersPerSecond, sprintSpeed);
        }

        private void Awake()
        {
            character = GetComponent<CharacterController>();
        }

        private void OnEnable()
        {
            SimulationClock.Ticked += OnSimulationTick;
        }

        private void OnDisable()
        {
            SimulationClock.Ticked -= OnSimulationTick;
            ReleasePointer();
        }

        private void Update()
        {
            moveInput = new Vector2(Input.GetAxisRaw("Horizontal"), Input.GetAxisRaw("Vertical"));
            sprintRequested = Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift);
            jumpRequested |= Input.GetKeyDown(KeyCode.Space);

            if (Input.GetMouseButtonDown(0) && !PointerCaptured)
            {
                CapturePointer();
            }
            else if (Input.GetKeyDown(KeyCode.Tab))
            {
                if (PointerCaptured)
                {
                    ReleasePointer();
                }
                else
                {
                    CapturePointer();
                }
            }

            if (PointerCaptured && viewCamera != null)
            {
                float yaw = Input.GetAxis("Mouse X") * mouseSensitivity * UnityEngine.Time.unscaledDeltaTime;
                float pitch = Input.GetAxis("Mouse Y") * mouseSensitivity * UnityEngine.Time.unscaledDeltaTime;
                transform.Rotate(Vector3.up, yaw, Space.World);
                cameraPitch = Mathf.Clamp(cameraPitch - pitch, -85f, 85f);
                viewCamera.transform.localRotation = Quaternion.Euler(cameraPitch, 0f, 0f);
            }
        }

        private void OnSimulationTick(double deltaSeconds)
        {
            if (character == null || !character.enabled)
            {
                return;
            }

            Vector3 localMove = new Vector3(moveInput.x, 0f, moveInput.y);
            localMove = Vector3.ClampMagnitude(localMove, 1f);
            Vector3 worldMove = transform.TransformDirection(localMove);
            float speed = sprintRequested ? sprintSpeedMetersPerSecond : walkSpeedMetersPerSecond;

            if (character.isGrounded && verticalVelocity < 0f)
            {
                verticalVelocity = -2f;
            }

            if (jumpRequested && character.isGrounded)
            {
                verticalVelocity = Mathf.Sqrt(2f * gravityMetersPerSecondSquared * jumpHeightMeters);
            }
            jumpRequested = false;
            verticalVelocity -= gravityMetersPerSecondSquared * (float)deltaSeconds;

            Vector3 velocity = worldMove * speed + Vector3.up * verticalVelocity;
            character.Move(velocity * (float)deltaSeconds);
        }

        private static void CapturePointer()
        {
            Cursor.lockState = CursorLockMode.Locked;
            Cursor.visible = false;
        }

        private static void ReleasePointer()
        {
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
        }
    }
}
